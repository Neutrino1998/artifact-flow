"""Artifact 操作工具和管理器（ArtifactManager + write-back cache）。

本文件原是 artifact 工具集大杂烩；按 "一文件一工具" 拆分进度：
- ``grep_artifact`` → ``tools/builtin/grep_artifact.py``
- ``update_artifact`` + Layer 0/1/2 匹配算法 → ``tools/builtin/update_artifact.py``
- ``create_artifact`` / ``read_artifact`` / ``rewrite_artifact`` 暂留此处
"""

import math
import secrets
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass
import re

from sqlalchemy.exc import IntegrityError

from config import config
from tools.artifact_envelope import ArtifactSlice, render_artifact_slice
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
from utils.logger import get_logger
from utils.text_slicing import count_lines, slice_lines_by_offset_limit

logger = get_logger("ArtifactFlow")


# Artifact ID 合法字符集：letter/digit/underscore + hyphen + dot，1-64 字符。
# envelope renderer 依赖此前提把 id 当受控值放入 XML attribute；create_artifact
# 入口校验，create_from_upload / persist_tool_result 通过 sanitize 保证生成
# 的 ID 满足该 pattern。
_ARTIFACT_ID_PATTERN = re.compile(r"^[\w\-.]{1,64}$")


def _normalize_filename_to_id(filename: str, max_base_len: int = 56) -> str:
    """文件名 → 合法 artifact_id base。

    保留扩展名（如果短），预留 8 字符给 dedup suffix（_NNNN.ext）让最终 ID
    仍在 64 字符内。全 Unicode 文件名降级为 "upload"。
    """
    base = re.sub(r'[^\w\-.]', '_', filename).lower()
    if not base:
        base = "upload"
    if len(base) <= max_base_len:
        return base
    name_part, dot, ext_part = base.rpartition('.')
    # 短扩展名保留：keep + '.' + ext == max_base_len
    if name_part and dot and 1 <= len(ext_part) <= 10:
        keep = max_base_len - len(ext_part) - 1
        if keep >= 1:
            return name_part[:keep] + '.' + ext_part
    return base[:max_base_len]




# ============================================================
# 内存对象（用于 diff-match-patch 处理）
# ============================================================

@dataclass
class ArtifactVersionMemory:
    """Artifact 版本记录（内存对象）"""
    version: int
    content: str
    updated_at: datetime
    update_type: str  # "create", "update", "update_fuzzy", "rewrite"
    changes: Optional[List[Tuple[str, str]]] = None  # [(old_str, new_str), ...]


class ArtifactMemory:
    """
    Artifact 内存对象

    用于处理 diff-match-patch 逻辑，与数据库模型分离。
    保持原有的模糊匹配能力。
    """

    def __init__(
        self,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        current_version: int = 1,
        metadata: Dict = None,
        created_at: Optional[datetime] = None,
        source: str = "agent"
    ):
        self.id = artifact_id
        self.content_type = content_type
        self.title = title
        self.content = content
        self.metadata = metadata or {}
        self.current_version = current_version
        self.created_at = created_at or datetime.now()
        self.updated_at = datetime.now()
        self.source = source


# ============================================================
# ArtifactManager（核心管理类）
# ============================================================

class ArtifactManager:
    """
    Artifact 管理器

    职责：
    - 协调内存 Artifact 和数据库持久化
    - 通过依赖注入接收 ArtifactRepository
    - 维护当前 session 的内存缓存
    - 执行期间只改内存，loop 结束统一 flush

    使用方式：
        async with db_manager.session() as session:
            repo = ArtifactRepository(session)
            manager = ArtifactManager(repo)
            await manager.create_artifact(...)

    类级别注册表 ``_active_managers`` 允许 REST API 在执行期间读到
    engine 内存中尚未 flush 的 artifact。执行开始时 register，
    flush_all 后 unregister。
    """

    # {session_id: ArtifactManager} — 进程级，执行期间注册
    _active_managers: Dict[str, "ArtifactManager"] = {}

    @classmethod
    def get_active(cls, session_id: str) -> Optional["ArtifactManager"]:
        """查找正在执行中的 ArtifactManager（REST API 用）"""
        return cls._active_managers.get(session_id)

    def register(self, session_id: str) -> None:
        """执行开始时注册，使 REST API 可访问内存中的 artifact"""
        ArtifactManager._active_managers[session_id] = self

    def unregister(self, session_id: str) -> None:
        """执行结束后注销"""
        ArtifactManager._active_managers.pop(session_id, None)

    def __init__(self, repository: Optional[ArtifactRepository] = None):
        self.repository = repository
        self._cache: Dict[str, Dict[str, ArtifactMemory]] = {}  # {session_id: {artifact_id: ArtifactMemory}}
        self._current_session_id: Optional[str] = None
        # Both `_dirty` and `_new` are insertion-ordered (dict-as-ordered-set) so
        # that iteration follows creation order, not Python's hash order:
        # - `list_artifacts` iterates `_new` to append yet-to-be-flushed artifacts —
        #   without insertion order, session-scope consumers (grep_artifact session
        #   cap truncation, etc.) shuffle which artifact wins a budget cap across runs.
        # - `flush_all` iterates `_dirty` to build INSERT order, which becomes the
        #   `created_at` server_default ordering for new rows; hash-ordered flush
        #   would leak hash randomization into post-flush DB ordering on the next turn.
        self._dirty: Dict[Tuple[str, str], None] = {}
        self._new: Dict[Tuple[str, str], None] = {}

    def _ensure_repository(self) -> ArtifactRepository:
        """确保 Repository 已设置"""
        if self.repository is None:
            raise RuntimeError("ArtifactManager: repository not configured")
        return self.repository

    def set_session(self, session_id: str) -> None:
        """设置当前 session 并注册到活跃管理器"""
        self._current_session_id = session_id
        if session_id not in self._cache:
            self._cache[session_id] = {}
        self.register(session_id)

    @property
    def current_session_id(self) -> Optional[str]:
        """获取当前 session ID"""
        return self._current_session_id

    def get_cached_artifacts(self, session_id: str) -> Dict[str, "ArtifactMemory"]:
        """返回指定 session 的内存缓存（只读，不触发 DB 查询）。

        供 REST API 在执行期间读取未 flush 的 artifact，
        避免共享 controller 的 DB session。
        """
        return self._cache.get(session_id, {})

    async def ensure_session_exists(self, session_id: str) -> None:
        """确保 ArtifactSession 存在（数据库层）"""
        repo = self._ensure_repository()
        await repo.ensure_session_exists(session_id)
        if session_id not in self._cache:
            self._cache[session_id] = {}

    async def create_artifact(
        self,
        session_id: str,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None,
        source: str = "agent"
    ) -> Tuple[bool, str]:
        """
        创建新的 Artifact（只写内存，flush_all 时持久化）
        """
        # ID 校验：限制为 word/hyphen/dot，1-64 字符。这是 envelope renderer
        # 把 id 放入 attribute 的安全前提，也和 create_from_upload 的 sanitize
        # 规则保持一致。
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Invalid artifact_id '{artifact_id}': must be 1-64 chars of "
                f"letters/digits/underscore/hyphen/dot only."
            )
        try:
            # 确保 session 存在
            await self.ensure_session_exists(session_id)

            # 检查缓存和 DB 中是否已存在
            if session_id in self._cache and artifact_id in self._cache[session_id]:
                return False, f"Artifact '{artifact_id}' already exists in session"

            repo = self._ensure_repository()
            existing = await repo.get_artifact(session_id, artifact_id)
            if existing:
                return False, f"Artifact '{artifact_id}' already exists in session"

            # 创建内存对象
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=1,
                metadata=metadata,
                source=source,
            )

            if session_id not in self._cache:
                self._cache[session_id] = {}
            self._cache[session_id][artifact_id] = memory

            # 标记为 dirty + new（dict-as-ordered-set 见 __init__ 注释）
            key = (session_id, artifact_id)
            self._dirty[key] = None
            self._new[key] = None

            logger.info(f"Created artifact '{artifact_id}' in session '{session_id}' (pending flush)")
            return True, f"Created artifact '{artifact_id}'"

        except NotFoundError as e:
            return False, str(e)
        except Exception as e:
            logger.exception(f"Failed to create artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}"

    async def persist_tool_result(
        self,
        session_id: str,
        tool_name: str,
        content: str,
    ) -> Tuple[str, int]:
        """
        把超长工具结果持久化为 artifact，返回 (artifact_id, version)。

        由引擎中间件调用（见 core/engine.py）。content_type 固定 text/plain，
        source 固定 "tool"。artifact_id 自动生成，避免和用户/agent 命名冲突。

        tool_name 可能含非法字符（MCP 工具的 `:`、`.`）或过长（自定义 HTTP
        工具名 50+ 字符），都会让 create_artifact 的 ID 校验拒绝。这里先
        sanitize + truncate 到安全范围，保证持久化路径不会因为名字格式问题
        fail-open 到原文回填——那是这个机制最不该出现的失败模式。
        """
        suffix = secrets.token_hex(6)  # 12 hex chars
        # 字符预算：64 - len("tool_") - len("_") - 12 = 46，留余量到 40
        safe_name = re.sub(r"[^\w\-.]", "_", tool_name)[:40]
        artifact_id = f"tool_{safe_name}_{suffix}"
        title = f"Output of {tool_name}"  # title 不受 ID 规则约束
        metadata = {
            "tool_name": tool_name,  # metadata 保留原始名字便于审计
            "persisted_at": datetime.now(timezone.utc).isoformat(),
        }
        success, message = await self.create_artifact(
            session_id=session_id,
            artifact_id=artifact_id,
            content_type="text/plain",
            title=title,
            content=content,
            metadata=metadata,
            source="tool",
        )
        if not success:
            raise RuntimeError(f"persist_tool_result failed: {message}")
        return artifact_id, 1

    async def create_from_upload(
        self,
        session_id: str,
        filename: str,
        content: str,
        content_type: str,
        metadata: Optional[Dict] = None
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Create artifact from user-uploaded file.
        Uploads are committed immediately (not deferred to flush_all).
        """
        # Generate base artifact_id from filename，保证 ≤ 56 字符 + 合法字符集
        artifact_id = _normalize_filename_to_id(filename)

        # Deduplicate: if ID already exists, append suffix
        repo = self._ensure_repository()
        suffix = 0
        original_id = artifact_id
        while True:
            existing = await repo.get_artifact(session_id, artifact_id)
            if not existing:
                break
            suffix += 1
            name_part, _, ext_part = original_id.rpartition('.')
            if name_part:
                artifact_id = f"{name_part}_{suffix}.{ext_part}"
            else:
                artifact_id = f"{original_id}_{suffix}"

        # 最终守门员：理论上 normalize + dedup（≤4 位后缀）总 ≤ 64，但留个
        # 防御性检查避免万一边界 bug 让脏 ID 进 DB
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Generated invalid artifact_id from filename {filename!r}: "
                f"{artifact_id!r} (must match {_ARTIFACT_ID_PATTERN.pattern})"
            ), None

        # Title from filename (without extension)
        import os
        title = os.path.splitext(filename)[0]

        upload_metadata = metadata or {}
        upload_metadata["original_filename"] = filename

        # Uploads commit immediately via repo
        try:
            await self.ensure_session_exists(session_id)
            db_artifact = await repo.create_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                metadata=upload_metadata,
                source="user_upload"
            )

            # Cache the memory object (not dirty — already persisted)
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=db_artifact.current_version,
                metadata=upload_metadata,
                created_at=db_artifact.created_at,
                source="user_upload",
            )
            if session_id not in self._cache:
                self._cache[session_id] = {}
            self._cache[session_id][artifact_id] = memory

            return True, f"Created artifact '{artifact_id}'", {
                "id": artifact_id,
                "session_id": session_id,
                "content_type": content_type,
                "title": title,
                "current_version": 1,
                "source": "user_upload",
                "original_filename": filename,
            }
        except DuplicateError:
            return False, f"Artifact '{artifact_id}' already exists in session", None
        except Exception as e:
            logger.exception(f"Failed to create upload artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}", None

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str
    ) -> Optional[ArtifactMemory]:
        """获取 Artifact（优先从缓存，miss 时从 DB 加载）"""
        # 1. 检查缓存
        if session_id in self._cache and artifact_id in self._cache[session_id]:
            return self._cache[session_id][artifact_id]

        # 2. 从数据库加载
        repo = self._ensure_repository()
        db_artifact = await repo.get_artifact(session_id, artifact_id)
        if not db_artifact:
            return None

        # 3. 创建内存对象并缓存
        memory = ArtifactMemory(
            artifact_id=db_artifact.id,
            content_type=db_artifact.content_type,
            title=db_artifact.title,
            content=db_artifact.content,
            current_version=db_artifact.current_version,
            metadata=db_artifact.metadata_,
            created_at=db_artifact.created_at,
            source=db_artifact.source,
        )

        if session_id not in self._cache:
            self._cache[session_id] = {}
        self._cache[session_id][artifact_id] = memory

        return memory

    async def update_artifact(
        self,
        session_id: str,
        artifact_id: str,
        old_str: str,
        new_str: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """更新 Artifact 内容（只改内存，标记 dirty）。

        third-tuple semantics:
        * on success → dict with ``match_type`` / ``similarity`` / ...
          (plus ``fuzzy_stats`` when Layer 2 ran)
        * on failure → ``None`` for "not found", or
          ``{"fuzzy_stats": ...}`` when Layer 2 bailed (so the tool
          layer can surface observability via ``ToolResult.metadata``)
        """
        # 局部 import 避免与 update_artifact.py 的 ArtifactManager 类型引用形成循环。
        from tools.builtin.update_artifact import compute_update

        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found", None

        info = compute_update(memory.content, old_str, new_str)

        if not info.success:
            failure_meta = (
                {"fuzzy_stats": info.fuzzy_stats} if info.fuzzy_stats else None
            )
            return False, info.message, failure_meta

        memory.content = info.new_content
        memory.current_version += 1
        memory.updated_at = datetime.now()
        memory.source = "agent"

        self._dirty[(session_id, artifact_id)] = None

        match_info: Dict[str, Any] = {
            "match_type": info.match_type,
            "similarity": info.similarity,
            "changes": info.changes,
        }
        if info.expected_text is not None:
            match_info["expected_text"] = info.expected_text
        if info.matched_text is not None:
            match_info["matched_text"] = info.matched_text
        if info.fuzzy_stats is not None:
            match_info["fuzzy_stats"] = info.fuzzy_stats

        return (
            True,
            f"Successfully updated artifact '{artifact_id}' (v{memory.current_version})",
            match_info,
        )

    async def rewrite_artifact(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str
    ) -> Tuple[bool, str]:
        """完全重写 Artifact 内容（只改内存，标记 dirty）"""
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found"

        memory.content = new_content
        memory.current_version += 1
        memory.updated_at = datetime.now()
        memory.source = "agent"

        self._dirty[(session_id, artifact_id)] = None

        return True, f"Successfully rewritten artifact '{artifact_id}' (v{memory.current_version})"

    async def read_artifact(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """读取 Artifact 内容"""
        if version is None:
            memory = await self.get_artifact(session_id, artifact_id)
            if not memory:
                return None

            return {
                "id": memory.id,
                "content_type": memory.content_type,
                "title": memory.title,
                "content": memory.content,
                "version": memory.current_version,
                "source": memory.source,
                "original_filename": (memory.metadata or {}).get("original_filename"),
                "created_at": memory.created_at.isoformat(),
                "updated_at": memory.updated_at.isoformat()
            }
        else:
            # 显式 version 读取：优先匹配 in-memory current_version。
            # 否则 read 一个还没 flush 的 artifact（如刚 web_fetch 持久化的）
            # 用它 envelope 里看到的 version=1 会 404，模型困惑。
            memory = await self.get_artifact(session_id, artifact_id)
            if memory and memory.current_version == version:
                return {
                    "id": memory.id,
                    "content_type": memory.content_type,
                    "title": memory.title,
                    "content": memory.content,
                    "version": memory.current_version,
                    "source": memory.source,
                    "original_filename": (memory.metadata or {}).get("original_filename"),
                    "created_at": memory.created_at.isoformat(),
                    "updated_at": memory.updated_at.isoformat()
                }

            # 不是当前版本 → 走 DB 取历史版本快照
            repo = self._ensure_repository()
            content = await repo.get_version_content(session_id, artifact_id, version)
            if content is None:
                return None

            return {
                "id": artifact_id,
                "content_type": memory.content_type if memory else "unknown",
                "title": memory.title if memory else "Unknown",
                "content": content,
                "version": version,
                "source": memory.source if memory else "agent",
                "created_at": memory.created_at.isoformat() if memory else None,
                "updated_at": None
            }

    async def flush_all(self, session_id: str, *, db_manager=None) -> None:
        """
        将所有 dirty artifacts 持久化到数据库。

        Write-back 语义：执行期间 create/update/rewrite 只改内存，flush_all
        在 engine loop 结束后统一持久化。同一轮执行内的多次编辑折叠为一个
        最终快照 — DB 只产生一条版本记录，版本号取内存中的 current_version。
        这意味着 ArtifactVersion 表的版本号可以是稀疏的（例如 v1 → v3，
        跳过了内存中的 v2），中间状态不可恢复，这是预期行为。

        - 新建的 artifact → repo.create_artifact(target_version=memory.current_version)
        - 已有的 artifact → repo.upsert_artifact_content(target_version=memory.current_version)

        When db_manager is provided, each artifact flush uses a fresh session + retry
        (resilient to DB transient failures). Dirty cache reads stay in this manager.

        Only clears entries that flush successfully.
        Raises on any failure so the caller can decide the terminal state.
        """
        if not self._dirty:
            self.unregister(session_id)
            return

        to_flush = [(sid, aid) for sid, aid in self._dirty if sid == session_id]
        failed: list = []

        try:
            for sid, aid in to_flush:
                memory = self._cache.get(sid, {}).get(aid)
                if not memory:
                    continue

                try:
                    await self._flush_one(sid, aid, memory, db_manager=db_manager)
                    # Success — remove from dirty/new
                    self._dirty.pop((sid, aid), None)
                    self._new.pop((sid, aid), None)
                    logger.info(f"Flushed artifact '{aid}' in session '{sid}'")
                except Exception as e:
                    logger.exception(f"Failed to flush artifact '{aid}': {e}")
                    failed.append((aid, e))
        finally:
            self.unregister(session_id)

        if failed:
            ids = ", ".join(aid for aid, _ in failed)
            raise RuntimeError(f"Failed to flush artifacts: {ids}")

    async def _flush_one(self, sid: str, aid: str, memory, *, db_manager=None) -> None:
        """Flush a single dirty artifact. Uses fresh session + retry when db_manager is provided."""
        is_new = (sid, aid) in self._new

        async def _write(repo):
            if is_new:
                await repo.create_artifact(
                    session_id=sid, artifact_id=aid,
                    content_type=memory.content_type, title=memory.title,
                    content=memory.content, metadata=memory.metadata,
                    source=memory.source, target_version=memory.current_version,
                )
            else:
                await repo.upsert_artifact_content(
                    session_id=sid, artifact_id=aid,
                    new_content=memory.content, update_type="update",
                    source=memory.source, target_version=memory.current_version,
                )

        if db_manager:
            async def _attempt(session):
                try:
                    await _write(ArtifactRepository(session))
                except (DuplicateError, IntegrityError):
                    # Previous retry attempt already committed — treat as success
                    logger.info(f"Artifact '{aid}' already persisted (duplicate), skipping")

            await db_manager.with_retry(_attempt)
        else:
            await _write(self._ensure_repository())

    async def get_version(self, session_id: str, artifact_id: str, version: int):
        """获取指定版本"""
        repo = self._ensure_repository()
        return await repo.get_version(session_id, artifact_id, version)

    async def list_versions(self, session_id: str, artifact_id: str):
        """列出 Artifact 的所有版本（ORM 对象列表）"""
        repo = self._ensure_repository()
        return await repo.list_versions(session_id, artifact_id)

    async def list_artifacts(
        self,
        session_id: str,
        content_type: Optional[str] = None,
        include_content: bool = True,
    ) -> List[Dict[str, Any]]:
        """列出 Session 的所有 Artifacts（序列化后的 dict）。

        Merges DB results with in-memory dirty/new artifacts so that
        the engine's context assembly sees same-run changes.
        """
        repo = self._ensure_repository()
        db_artifacts = await repo.list_artifacts(
            session_id=session_id,
            content_type=content_type,
        )

        # Build result from DB, keyed by id for merging
        seen_ids: set = set()
        result = []
        for art in db_artifacts:
            # If we have a dirty in-memory version, prefer it
            memory = self._cache.get(session_id, {}).get(art.id)
            if memory and (session_id, art.id) in self._dirty:
                if content_type and memory.content_type != content_type:
                    continue
                info = self._serialize_memory(memory, session_id, include_content)
            else:
                info: Dict[str, Any] = {
                    "id": art.id,
                    "content_type": art.content_type,
                    "title": art.title,
                    "version": art.current_version,
                    "source": art.source,
                    "original_filename": (art.metadata_ or {}).get("original_filename"),
                    "created_at": art.created_at.isoformat(),
                    "updated_at": art.updated_at.isoformat(),
                }
                if include_content:
                    info["content"] = art.content
            result.append(info)
            seen_ids.add(art.id)

        # Append in-memory new artifacts not yet in DB
        for sid, aid in self._new:
            if sid != session_id or aid in seen_ids:
                continue
            memory = self._cache.get(sid, {}).get(aid)
            if not memory:
                continue
            if content_type and memory.content_type != content_type:
                continue
            result.append(self._serialize_memory(memory, session_id, include_content))

        return result

    @staticmethod
    def _serialize_memory(
        memory: 'ArtifactMemory', session_id: str, include_content: bool
    ) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "id": memory.id,
            "content_type": memory.content_type,
            "title": memory.title,
            "version": memory.current_version,
            "source": memory.source,
            "original_filename": (memory.metadata or {}).get("original_filename"),
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
        }
        if include_content:
            info["content"] = memory.content
        return info


# ============================================================
# 工具类
# ============================================================

class CreateArtifactTool(BaseTool):
    """创建 Artifact 工具"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="create_artifact",
            description="Create a new artifact. Check existing artifacts first to avoid duplicates.",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Unique identifier (e.g., 'task_plan', 'research_report')",
                required=True
            ),
            ToolParameter(
                name="content_type",
                type="string",
                description="MIME type of the artifact content",
                required=False,
                default="text/markdown",
                enum=["text/markdown", "text/plain", "text/x-python", "text/html", "application/json", "text/javascript", "text/yaml"]
            ),
            ToolParameter(
                name="title",
                type="string",
                description="Title of the artifact",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="Initial text content",
                required=True
            )
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._manager.create_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            content_type=params["content_type"],  # 默认值已由 _apply_defaults 填充
            title=params["title"],
            content=params["content"]
        )

        if success:
            logger.info(message)
            return ToolResult(
                success=True,
                data=f'<artifact version="1"><id>{params["id"]}</id> {message}</artifact>',
            )
        return ToolResult(success=False, error=message)



class RewriteArtifactTool(BaseTool):
    """重写 Artifact 工具（完全替换内容）"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="rewrite_artifact",
            description="Completely replace artifact content. Use when changes are too extensive for update_artifact.",
            permission=ToolPermission.AUTO
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to rewrite",
                required=True
            ),
            ToolParameter(
                name="content",
                type="string",
                description="New complete content",
                required=True
            )
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message = await self._manager.rewrite_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            new_content=params["content"]
        )

        if success:
            logger.info(message)
            memory = await self._manager.get_artifact(session_id, params["id"])
            version = memory.current_version if memory else None
            return ToolResult(
                success=True,
                data=f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>',
            )

        return ToolResult(success=False, error=message)


class ReadArtifactTool(BaseTool):
    """读取 Artifact 工具"""

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="read_artifact",
            description=(
                "Read artifact content with optional line-based pagination. "
                "Returns content wrapped in <artifact_slice> with metadata "
                "(shown_lines/total_lines/has_more). When has_more=true, use "
                "the offset hint to read the next slice."
            ),
            permission=ToolPermission.AUTO,
            # Infinity = 永不落盘。read_artifact 自身的输出若被中间件再次落盘，
            # 会形成 Read→artifact→Read 循环。
            max_result_size_chars=math.inf,
        )
        self._manager = manager

    def set_manager(self, manager: ArtifactManager) -> None:
        """设置 ArtifactManager（依赖注入）"""
        self._manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to read",
                required=True
            ),
            ToolParameter(
                name="version",
                type="integer",
                description="Version number (optional, defaults to latest)",
                required=False,
                default=None
            ),
            ToolParameter(
                name="offset",
                type="integer",
                description="1-indexed start line (omit to read from line 1)",
                required=False,
                default=1,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description=(
                    "Maximum lines to read (omit to read until built-in size cap). "
                    "Use the offset hint from a prior call to continue reading large artifacts."
                ),
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        result = await self._manager.read_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            version=params.get("version")
        )

        if result is None:
            version = params.get("version")
            if version:
                return ToolResult(success=False, error=f"Version {version} not found")
            return ToolResult(success=False, error=f"Artifact '{params['id']}' not found")

        artifact_id = result.get("id", "")
        content_type = result.get("content_type", "")
        title = result.get("title", "")
        version_num = result.get("version", 1)
        source = result.get("source", "agent")
        updated_at = result.get("updated_at", "")
        content = result.get("content", "") or ""

        offset = params.get("offset") or 1
        limit = params.get("limit")  # None = 读到 char_cap
        explicit_version = params.get("version")  # None = latest

        body, shown_lines, truncated_by, has_more = slice_lines_by_offset_limit(
            content,
            offset=offset,
            limit=limit,
            char_cap=config.READ_ARTIFACT_MAX_CHARS,
        )
        total_lines = count_lines(content)

        hint = None
        if has_more and shown_lines is not None:
            # 透传调用者原始的 limit / version：避免续读悄悄换页大小或跳到 latest 版本
            next_offset = shown_lines[1] + 1
            cont_args = [f"id='{artifact_id}'", f"offset={next_offset}"]
            if limit is not None:
                cont_args.append(f"limit={limit}")
            if explicit_version is not None:
                cont_args.append(f"version={explicit_version}")
            hint = f"To continue: read_artifact({', '.join(cont_args)})"

        slice = ArtifactSlice(
            id=artifact_id,
            version=version_num,
            content_type=content_type,
            source=source,
            title=title,
            body=body,
            total_chars=len(content),
            shown_chars=len(body),
            total_lines=total_lines,
            shown_lines=shown_lines,
            truncated_by=truncated_by,
            has_more=has_more,
            hint=hint,
            updated_at=updated_at,
        )
        return ToolResult(success=True, data=render_artifact_slice(slice))


# ============================================================
# 工厂函数
# ============================================================

def create_artifact_tools(manager: ArtifactManager) -> List[BaseTool]:
    """创建所有 Artifact 工具（工厂函数）。

    GrepArtifactTool / UpdateArtifactTool 局部 import 以避免与各自模块
    对 ``ArtifactManager`` 的类型引用形成包级循环。
    """
    from tools.builtin.grep_artifact import GrepArtifactTool
    from tools.builtin.update_artifact import UpdateArtifactTool

    return [
        CreateArtifactTool(manager),
        UpdateArtifactTool(manager),
        RewriteArtifactTool(manager),
        ReadArtifactTool(manager),
        GrepArtifactTool(manager),
    ]


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    import asyncio
    from db.database import create_test_database_manager
    from repositories.artifact_repo import ArtifactRepository

    async def run_tests():
        """测试 ArtifactManager"""
        print("\n🧪 ArtifactManager Test Suite")
        print("=" * 60)

        # 创建测试数据库
        db = create_test_database_manager()
        await db.initialize()

        try:
            async with db.session() as session:
                # 创建 Repository 和 Manager
                repo = ArtifactRepository(session)
                manager = ArtifactManager(repo)

                # 设置 session
                session_id = "test-session-001"
                manager.set_session(session_id)
                await manager.ensure_session_exists(session_id)

                print(f"✅ Created manager for session: {session_id}")

                # 测试创建
                success, msg = await manager.create_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    content_type="text/markdown",
                    title="Test Plan",
                    content="# Task Plan\n\n1. [✗] Step 1\n2. [✗] Step 2"
                )
                print(f"✅ Create: {msg}")

                # 测试读取
                result = await manager.read_artifact(session_id, "task_plan")
                print(f"✅ Read: version={result['version']}")

                # 测试精确匹配更新
                success, msg, info = await manager.update_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    old_str="1. [✗] Step 1",
                    new_str="1. [✓] Step 1 - completed"
                )
                print(f"✅ Update (exact): {msg}")

                # 测试模糊匹配更新
                success, msg, info = await manager.update_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    old_str="2. [x] Step 2",  # 故意写错
                    new_str="2. [✓] Step 2 - done"
                )
                if success:
                    print(f"✅ Update (fuzzy): {msg}")
                else:
                    print(f"⚠️ Fuzzy match failed (expected): {msg}")

                # 测试重写
                success, msg = await manager.rewrite_artifact(
                    session_id=session_id,
                    artifact_id="task_plan",
                    new_content="# New Plan\n\nCompletely rewritten."
                )
                print(f"✅ Rewrite: {msg}")

                # 测试列表
                artifacts = await manager.list_artifacts(session_id)
                print(f"✅ List: {len(artifacts)} artifacts")

                print("\n" + "=" * 60)
                print("✅ All tests passed!")

        finally:
            await db.close()

    asyncio.run(run_tests())
