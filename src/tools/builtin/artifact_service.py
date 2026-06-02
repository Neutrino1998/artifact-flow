"""ArtifactService —— artifact 层的薄编排(无状态业务逻辑)。

承接原 ``ArtifactManager`` 五职责里的:③repo 持有 / ④业务编排 / ⑤序列化,
另加**发事件**。**纯状态(缓存 + dirty/new)归 ``ArtifactWorkingSet``**,
**纯算法(模糊匹配 ``compute_update`` / grep 扫描)留各自模块**——Service 只
**调用**,不内联。

实例化关系(关键):每个 Service **独占**自己的 ``ArtifactWorkingSet``,**不共享**。
旧 ``_active_managers`` 进程级注册表已删除——它在多 worker 下静默失效(REST live
overlay 跨 worker 读不到执行 worker 的内存态)。现在:
- **执行轮**:控制器持有一个 Service,WorkingSet 随 turn 填充;引擎在 loop 起点
  ``bind_emit`` 注入事件回调,create/update/rewrite/上传 stage 经此发 ``ARTIFACT_*``。
- **REST 请求**:``get_artifact_service`` 每请求新建一个 Service,WorkingSet 恒空、
  无 emit → 读取自然落到纯 DB(turn 中故意落后于 live,由前端事件流补,见 plan 决策 6)。
"""

import math  # noqa: F401  (保留:历史上 ReadArtifactTool 用过,避免无意义 churn——实际由 artifact_ops 持有)
import os
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError

from config import config
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
from tools.builtin.artifact_working_set import ArtifactMemory, ArtifactWorkingSet
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")

# Artifact live 事件类型值(string)。**不在顶层 import core.events**:tools 包被
# core 先 import,顶层引 core 会触发 core/__init__ → controller → 本模块的循环
# import。这两个常量与 ``StreamEventType.ARTIFACT_CREATED/UPDATED.value`` 对齐,
# 由 tests/tools/builtin/test_artifact_events.py 交叉校验防漂移(同终态事件做法)。
_EVT_ARTIFACT_CREATED = "artifact_created"
_EVT_ARTIFACT_UPDATED = "artifact_updated"


# Artifact ID 合法字符集:letter/digit/underscore + hyphen + dot,1-64 字符。
# envelope renderer 依赖此前提把 id 当受控值放入 XML attribute;create_artifact
# 入口校验,create_from_upload / persist_tool_result 通过 sanitize 保证生成的 ID
# 满足该 pattern。
_ARTIFACT_ID_PATTERN = re.compile(r"^[\w\-.]{1,64}$")


def _normalize_filename_to_id(filename: str, max_base_len: int = 56) -> str:
    """文件名 → 合法 artifact_id base。

    保留扩展名(如果短),预留 8 字符给 dedup suffix(_NNNN.ext)让最终 ID
    仍在 64 字符内。全 Unicode 文件名降级为 "upload"。
    """
    base = re.sub(r'[^\w\-.]', '_', filename).lower()
    if not base:
        base = "upload"
    if len(base) <= max_base_len:
        return base
    name_part, dot, ext_part = base.rpartition('.')
    # 短扩展名保留:keep + '.' + ext == max_base_len
    if name_part and dot and 1 <= len(ext_part) <= 10:
        keep = max_base_len - len(ext_part) - 1
        if keep >= 1:
            return name_part[:keep] + '.' + ext_part
    return base[:max_base_len]


class ArtifactService:
    """Artifact 用例编排:dedup / 版本计算 / 调纯算法 / 上传 stage / tool-result
    持久化 / flush(WorkingSet→Repo)/ 发事件。无 artifact 领域状态(那在 WorkingSet)。

    用法:
        repo = ArtifactRepository(session)
        service = ArtifactService(repo)          # 自带一个独占 WorkingSet
        service.set_session(session_id)
        await service.create_artifact(...)
    """

    def __init__(
        self,
        repository: Optional[ArtifactRepository] = None,
        working_set: Optional[ArtifactWorkingSet] = None,
    ):
        self.repository = repository
        self._ws = working_set or ArtifactWorkingSet()
        # turn 级事件回调(引擎 bind);REST 侧恒 None → 不发事件。详见模块 docstring。
        self._emit = None
        # 本 turn 已发过「整文 live 事件」(故前端已有 base)的 artifact id 集合。
        # update 只有在 id 已在此集合时才发 span delta,否则发整文——保证前端**永不**
        # 收到无 base 可应用的 delta,从而无需中途查 DB 取 base(守原则 1:中途不查 DB
        # 求 live 态)。bind_emit(真回调)时按 turn 重置。
        self._emitted_base: set = set()

    # ========================================
    # 接线 / 状态委托
    # ========================================

    def bind_emit(self, emit) -> None:
        """引擎在 execute_loop 起点注入 ``_emit`` 闭包,loop 末传 None 解绑,
        防止跨 turn 持有失效闭包。REST 永不调用此方法。"""
        self._emit = emit
        if emit is not None:
            self._emitted_base = set()  # 每 turn 重置 base 跟踪

    async def _emit_artifact(self, event_type: str, data: Dict[str, Any]) -> None:
        """发 SSE-only artifact 事件(仅当 emit 已 bind,即执行轮)。REST 侧 _emit
        为 None → no-op。agent=None:artifact 事件按 id 归并,不依赖 agent,且 SSE-only
        不入历史。"""
        if self._emit is None:
            return
        await self._emit(event_type, None, data, sse_only=True)

    @staticmethod
    def _content_payload(content: str) -> Dict[str, Any]:
        """整文事件载荷:超 live 上限则省略正文、只发已变更信号(靠 COMPLETE 后 DB
        对齐补全),否则带整文。"""
        if len(content) > config.ARTIFACT_LIVE_CONTENT_MAX_CHARS:
            return {"content_omitted": True}
        return {"content": content}

    def _note_base(self, artifact_id: str, payload: Dict[str, Any]) -> None:
        """记录前端是否已拿到该 artifact 的整文 base:带 content 则前端有 base
        (后续 update 可发 delta);content_omitted(超限)则前端没有 base,下次仍发整文。"""
        if "content" in payload:
            self._emitted_base.add(artifact_id)
        else:
            self._emitted_base.discard(artifact_id)

    async def _register_new(self, session_id: str, memory: ArtifactMemory) -> None:
        """把一个新建的 ArtifactMemory 落入 WorkingSet(mark_new → 随 flush_all 落库)
        并发 ARTIFACT_CREATED。模型自建与用户上传共用此路径(统一生命周期)。"""
        self._ws.put(session_id, memory)
        self._ws.mark_new(session_id, memory.id)
        payload = self._content_payload(memory.content)
        await self._emit_artifact(_EVT_ARTIFACT_CREATED, {
            "id": memory.id,
            "title": memory.title,
            "content_type": memory.content_type,
            "source": memory.source,
            "current_version": memory.current_version,
            **payload,
        })
        self._note_base(memory.id, payload)

    @property
    def working_set(self) -> ArtifactWorkingSet:
        return self._ws

    @property
    def current_session_id(self) -> Optional[str]:
        return self._ws.current_session_id

    def set_session(self, session_id: str) -> None:
        """设置当前 session(供工具读取 current_session_id)。"""
        self._ws.set_session(session_id)

    def _ensure_repository(self) -> ArtifactRepository:
        if self.repository is None:
            raise RuntimeError("ArtifactService: repository not configured")
        return self.repository

    async def ensure_session_exists(self, session_id: str) -> None:
        """确保 ArtifactSession 存在(数据库层)。"""
        repo = self._ensure_repository()
        await repo.ensure_session_exists(session_id)

    # ========================================
    # 创建
    # ========================================

    async def create_artifact(
        self,
        session_id: str,
        artifact_id: str,
        content_type: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None,
        source: str = "agent",
    ) -> Tuple[bool, str]:
        """创建新的 Artifact(只写 WorkingSet,flush_all 时持久化)。"""
        # ID 校验:限制为 word/hyphen/dot,1-64 字符。这是 envelope renderer 把 id
        # 放入 attribute 的安全前提,也和 create_from_upload 的 sanitize 规则一致。
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Invalid artifact_id '{artifact_id}': must be 1-64 chars of "
                f"letters/digits/underscore/hyphen/dot only."
            )
        try:
            await self.ensure_session_exists(session_id)

            # 检查缓存和 DB 中是否已存在
            if self._ws.peek(session_id, artifact_id) is not None:
                return False, f"Artifact '{artifact_id}' already exists in session"

            repo = self._ensure_repository()
            existing = await repo.get_artifact(session_id, artifact_id)
            if existing:
                return False, f"Artifact '{artifact_id}' already exists in session"

            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=1,
                metadata=metadata,
                source=source,
            )
            await self._register_new(session_id, memory)

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
        """把超长工具结果持久化为 artifact,返回 (artifact_id, version)。

        由引擎中间件调用(见 core/engine.py)。content_type 固定 text/plain,
        source 固定 "tool"。artifact_id 自动生成,避免和用户/agent 命名冲突。

        tool_name 可能含非法字符(MCP 工具的 ``:``、``.``)或过长(自定义 HTTP
        工具名 50+ 字符),都会让 create_artifact 的 ID 校验拒绝。这里先 sanitize +
        truncate 到安全范围,保证持久化路径不会因为名字格式问题 fail-open 到原文
        回填——那是这个机制最不该出现的失败模式。
        """
        suffix = secrets.token_hex(6)  # 12 hex chars
        # 字符预算:64 - len("tool_") - len("_") - 12 = 46,留余量到 40
        safe_name = re.sub(r"[^\w\-.]", "_", tool_name)[:40]
        artifact_id = f"tool_{safe_name}_{suffix}"
        title = f"Output of {tool_name}"  # title 不受 ID 规则约束
        metadata = {
            "tool_name": tool_name,  # metadata 保留原始名字便于审计
            "persisted_at": utc_now().isoformat(),
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
        metadata: Optional[Dict] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """从用户上传文件 **stage** 一个 artifact 进 WorkingSet(不即时 commit)。

        与模型自建走同一 write-back 路径:mark_new → 发 ARTIFACT_CREATED → 随 turn 末
        flush_all 落库。由 execute_loop 在 turn 起点调用(uploads closure-carry 进引擎)。
        因此上传 turn 中途死 = 与模型产物一致地丢失(ephemeral 语义),用户侧由前端
        staged 文件保留到 COMPLETE 兜底。`_normalize` + dedup 在此完成。
        """
        artifact_id = _normalize_filename_to_id(filename)

        # Dedup:同时对 WorkingSet(本轮已 stage / 缓存)与 DB(往轮已落库)去重,
        # 追加后缀直到唯一。
        repo = self._ensure_repository()
        suffix = 0
        original_id = artifact_id
        while (
            self._ws.peek(session_id, artifact_id) is not None
            or await repo.get_artifact(session_id, artifact_id) is not None
        ):
            suffix += 1
            name_part, _, ext_part = original_id.rpartition('.')
            if name_part:
                artifact_id = f"{name_part}_{suffix}.{ext_part}"
            else:
                artifact_id = f"{original_id}_{suffix}"

        # 最终守门员:理论上 normalize + dedup(≤4 位后缀)总 ≤ 64,但留个防御性
        # 检查避免万一边界 bug 让脏 ID 进 DB
        if not _ARTIFACT_ID_PATTERN.match(artifact_id):
            return False, (
                f"Generated invalid artifact_id from filename {filename!r}: "
                f"{artifact_id!r} (must match {_ARTIFACT_ID_PATTERN.pattern})"
            ), None

        title = os.path.splitext(filename)[0]
        upload_metadata = dict(metadata or {})
        upload_metadata["original_filename"] = filename

        try:
            await self.ensure_session_exists(session_id)
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=1,
                metadata=upload_metadata,
                source="user_upload",
            )
            await self._register_new(session_id, memory)

            return True, f"Created artifact '{artifact_id}'", {
                "id": artifact_id,
                "session_id": session_id,
                "content_type": content_type,
                "title": title,
                "current_version": 1,
                "source": "user_upload",
                "original_filename": filename,
            }
        except Exception as e:
            logger.exception(f"Failed to stage upload artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}", None

    # ========================================
    # 读取
    # ========================================

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str,
    ) -> Optional[ArtifactMemory]:
        """获取 Artifact(优先从缓存,miss 时从 DB 加载并回填缓存)。"""
        cached = self._ws.peek(session_id, artifact_id)
        if cached is not None:
            return cached

        repo = self._ensure_repository()
        db_artifact = await repo.get_artifact(session_id, artifact_id)
        if not db_artifact:
            return None

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
        self._ws.put(session_id, memory)
        return memory

    async def read_artifact(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """读取 Artifact 内容(序列化为 dict)。"""
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
                "updated_at": memory.updated_at.isoformat(),
            }

        # 显式 version 读取:优先匹配 in-memory current_version。否则 read 一个还没
        # flush 的 artifact(如刚 web_fetch 持久化的)用它 envelope 里看到的 version=1
        # 会 404,模型困惑。
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
                "updated_at": memory.updated_at.isoformat(),
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
            "updated_at": None,
        }

    # ========================================
    # 更新 / 重写
    # ========================================

    async def update_artifact(
        self,
        session_id: str,
        artifact_id: str,
        old_str: str,
        new_str: str,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """更新 Artifact 内容(只改 WorkingSet,标记 dirty)。

        third-tuple semantics:
        * on success → dict with ``match_type`` / ``similarity`` / ...
          (plus ``fuzzy_stats`` when Layer 2 ran)
        * on failure → ``None`` for "not found", or ``{"fuzzy_stats": ...}`` when
          Layer 2 bailed(供工具层经 ``ToolResult.metadata`` surface 可观测)。
        """
        # 局部 import 避免与 update_artifact.py 的类型引用形成循环。
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
        memory.updated_at = utc_now()
        memory.source = "agent"

        self._ws.mark_dirty(session_id, artifact_id)

        # 仅当前端已有 base(本 turn 发过该 id 的整文)且命中 span 可用时,发权威
        # span delta(取自 compute_update,前端无法从 params 反推模糊命中位置);否则
        # (首次触碰本 turn 的旧 artifact / span 缺失)发整文,确保前端先有 base。
        has_base = artifact_id in self._emitted_base
        if has_base and info.offset is not None and info.deleted_len is not None:
            update_payload: Dict[str, Any] = {
                "delta": {
                    "offset": info.offset,
                    "deleted_len": info.deleted_len,
                    "inserted_text": new_str,
                },
            }
        else:
            update_payload = self._content_payload(memory.content)
            self._note_base(artifact_id, update_payload)
        await self._emit_artifact(_EVT_ARTIFACT_UPDATED, {
            "id": artifact_id,
            "current_version": memory.current_version,
            **update_payload,
        })

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
        new_content: str,
    ) -> Tuple[bool, str]:
        """完全重写 Artifact 内容(只改 WorkingSet,标记 dirty)。"""
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found"

        memory.content = new_content
        memory.current_version += 1
        memory.updated_at = utc_now()
        memory.source = "agent"

        self._ws.mark_dirty(session_id, artifact_id)

        # rewrite = 整文替换:发整文(无 span delta),刷新 base。
        rewrite_payload = self._content_payload(new_content)
        await self._emit_artifact(_EVT_ARTIFACT_UPDATED, {
            "id": artifact_id,
            "current_version": memory.current_version,
            **rewrite_payload,
        })
        self._note_base(artifact_id, rewrite_payload)

        return True, f"Successfully rewritten artifact '{artifact_id}' (v{memory.current_version})"

    # ========================================
    # 持久化(flush)
    # ========================================

    async def flush_all(self, session_id: str, *, db_manager=None) -> None:
        """将所有 dirty artifacts 持久化到数据库。

        Write-back 语义:执行期间 create/update/rewrite 只改 WorkingSet,flush_all
        在 engine loop 结束后统一持久化。同一轮执行内的多次编辑折叠为一个最终快照——
        DB 只产生一条版本记录,版本号取内存中的 current_version。这意味着
        ArtifactVersion 表的版本号可以是稀疏的(例如 v1 → v3),中间状态不可恢复,
        这是预期行为。

        - 新建的 artifact → repo.create_artifact(target_version=memory.current_version)
        - 已有的 artifact → repo.upsert_artifact_content(target_version=memory.current_version)

        db_manager 提供时,每个 artifact flush 使用 fresh session + retry(对 DB 瞬时
        失败有韧性)。只清除 flush 成功的条目。任一失败则 raise,由调用方决定终态。
        """
        if not self._ws.has_dirty():
            return

        to_flush = self._ws.dirty_keys(session_id)
        failed: list = []

        for sid, aid in to_flush:
            memory = self._ws.peek(sid, aid)
            if not memory:
                continue
            try:
                await self._flush_one(sid, aid, memory, db_manager=db_manager)
                self._ws.clear_one(sid, aid)
                logger.info(f"Flushed artifact '{aid}' in session '{sid}'")
            except Exception as e:
                logger.exception(f"Failed to flush artifact '{aid}': {e}")
                failed.append((aid, e))

        if failed:
            ids = ", ".join(aid for aid, _ in failed)
            raise RuntimeError(f"Failed to flush artifacts: {ids}")

    async def _flush_one(self, sid: str, aid: str, memory, *, db_manager=None) -> None:
        """Flush 单个 dirty artifact。db_manager 提供时用 fresh session + retry。"""
        is_new = self._ws.is_new(sid, aid)

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
                    # 前一次 retry 已提交 — 视作成功
                    logger.info(f"Artifact '{aid}' already persisted (duplicate), skipping")

            await db_manager.with_retry(_attempt)
        else:
            await _write(self._ensure_repository())

    # ========================================
    # 版本 / 列表
    # ========================================

    async def get_version(self, session_id: str, artifact_id: str, version: int):
        """获取指定版本(ORM 对象)。"""
        repo = self._ensure_repository()
        return await repo.get_version(session_id, artifact_id, version)

    async def list_versions(self, session_id: str, artifact_id: str):
        """列出 Artifact 的所有版本(ORM 对象列表)。"""
        repo = self._ensure_repository()
        return await repo.list_versions(session_id, artifact_id)

    async def list_artifacts(
        self,
        session_id: str,
        content_type: Optional[str] = None,
        include_content: bool = True,
    ) -> List[Dict[str, Any]]:
        """列出 Session 的所有 Artifacts(序列化后的 dict)。

        合并 DB 结果与 WorkingSet 里的 dirty/new artifact,使引擎 context 装配看到
        同轮改动。REST 侧 WorkingSet 恒空,故等价纯 DB 列表。
        """
        repo = self._ensure_repository()
        db_artifacts = await repo.list_artifacts(
            session_id=session_id,
            content_type=content_type,
        )

        seen_ids: set = set()
        result = []
        for art in db_artifacts:
            memory = self._ws.peek(session_id, art.id)
            if memory and self._ws.is_dirty(session_id, art.id):
                if content_type and memory.content_type != content_type:
                    continue
                info = self._serialize_memory(memory, include_content)
            else:
                info = {
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

        # 追加 WorkingSet 里尚未落 DB 的 new artifact
        for sid, aid in self._ws.new_keys(session_id):
            if aid in seen_ids:
                continue
            memory = self._ws.peek(sid, aid)
            if not memory:
                continue
            if content_type and memory.content_type != content_type:
                continue
            result.append(self._serialize_memory(memory, include_content))

        return result

    @staticmethod
    def _serialize_memory(memory: ArtifactMemory, include_content: bool) -> Dict[str, Any]:
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
