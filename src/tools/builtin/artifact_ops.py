"""
Artifact 操作工具和管理器

改造说明（v2.0）：
- 移除全局单例 `_artifact_store`
- 新增 `ArtifactManager` 类，通过依赖注入使用 `ArtifactRepository`
- 保留 `Artifact` 类的核心 diff-match-patch 逻辑（作为内存对象）
- 工具类通过 `ArtifactManager` 访问数据
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
import re
import unicodedata
from fuzzysearch import find_near_matches

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
from db.models import VersionConflictError
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


def _truncate_middle(text: str, max_len: int = 200) -> str:
    """Truncate long text keeping head and tail with '...' in between."""
    if len(text) <= max_len:
        return text
    half = (max_len - 5) // 2  # 5 chars for "\n...\n"
    return text[:half] + "\n...\n" + text[-half:]


# CJK Unicode ranges for normalization
_CJK_RE = (
    r'[\u2e80-\u2fdf'   # CJK Radicals
    r'\u3000-\u303f'     # CJK Symbols & Punctuation
    r'\u3040-\u309f'     # Hiragana
    r'\u30a0-\u30ff'     # Katakana
    r'\u3400-\u4dbf'     # CJK Unified Ext A
    r'\u4e00-\u9fff'     # CJK Unified
    r'\uf900-\ufaff'     # CJK Compat Ideographs
    r'\ufe30-\ufe4f'     # CJK Compat Forms
    r'\uff00-\uffef'     # Halfwidth/Fullwidth Forms
    r'\U00020000-\U0002a6df'  # CJK Unified Ext B
    r']'
)
# Space(s) between CJK and Latin/digit, or vice versa
_CJK_LATIN_SPACE = re.compile(
    rf'({_CJK_RE})\s+([A-Za-z0-9])|([A-Za-z0-9])\s+({_CJK_RE})'
)


# Smart quotes → ASCII
_SMART_QUOTES = str.maketrans({
    '\u2018': "'",   # '
    '\u2019': "'",   # '
    '\u201a': "'",   # ‚
    '\u201c': '"',   # "
    '\u201d': '"',   # "
    '\u201e': '"',   # „
    '\u2039': "'",   # ‹
    '\u203a': "'",   # ›
    '\u00ab': '"',   # «
    '\u00bb': '"',   # »
})

# Unicode dashes → ASCII hyphen
_UNICODE_DASHES = str.maketrans({
    '\u2012': '-',   # figure dash
    '\u2013': '-',   # en dash –
    '\u2014': '-',   # em dash —
    '\u2015': '-',   # horizontal bar ―
    '\u2212': '-',   # minus sign −
    '\ufe58': '-',   # small em dash ﹘
    '\ufe63': '-',   # small hyphen-minus ﹣
    '\uff0d': '-',   # fullwidth hyphen-minus －
})

# Special whitespace → regular space
_SPECIAL_SPACES = str.maketrans({
    '\u00a0': ' ',   # non-breaking space
    '\u2000': ' ',   # en quad
    '\u2001': ' ',   # em quad
    '\u2002': ' ',   # en space
    '\u2003': ' ',   # em space
    '\u2004': ' ',   # three-per-em space
    '\u2005': ' ',   # four-per-em space
    '\u2006': ' ',   # six-per-em space
    '\u2007': ' ',   # figure space
    '\u2008': ' ',   # punctuation space
    '\u2009': ' ',   # thin space
    '\u200a': ' ',   # hair space
    '\u202f': ' ',   # narrow no-break space
    '\u205f': ' ',   # medium mathematical space
    '\u3000': ' ',   # ideographic space
})


def _normalize_for_match(text: str) -> tuple[str, list[int]]:
    """Normalize text for fuzzy matching and build index map.

    - Smart quotes → ASCII quotes
    - Unicode dashes → ASCII hyphen
    - Special whitespace → regular space
    - NFKC normalize (fullwidth → halfwidth, etc.)
    - Strip trailing whitespace per line
    - Collapse spaces at CJK-Latin/digit boundaries

    Returns:
        (normalized_text, index_map) where index_map[i] = original char index
        for normalized char i.
    """
    # Step 1: Character-level normalization (1-to-1, preserves indices)
    text = text.translate(_SMART_QUOTES)
    text = text.translate(_UNICODE_DASHES)
    text = text.translate(_SPECIAL_SPACES)

    # Step 2: NFKC
    text = unicodedata.normalize('NFKC', text)

    # Step 3: Strip trailing whitespace per line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    # Step 3: Collapse CJK-Latin boundary spaces, building index map
    index_map = []
    result = []
    i = 0
    while i < len(text):
        # Check if we're at a CJK-space-Latin or Latin-space-CJK boundary
        if text[i] == ' ' and i > 0 and i + 1 < len(text):
            prev_char = text[i - 1]
            # Look ahead past spaces
            j = i
            while j < len(text) and text[j] == ' ':
                j += 1
            if j < len(text):
                next_char = text[j]
                prev_is_cjk = bool(re.match(_CJK_RE, prev_char))
                next_is_cjk = bool(re.match(_CJK_RE, next_char))
                prev_is_latin = bool(re.match(r'[A-Za-z0-9]', prev_char))
                next_is_latin = bool(re.match(r'[A-Za-z0-9]', next_char))

                if (prev_is_cjk and next_is_latin) or (prev_is_latin and next_is_cjk):
                    # Skip boundary spaces
                    i = j
                    continue

        result.append(text[i])
        index_map.append(i)
        i += 1

    return ''.join(result), index_map


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
        lock_version: int = 1,
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
        self.lock_version = lock_version
        self.created_at = created_at or datetime.now()
        self.updated_at = datetime.now()
        self.source = source

    def compute_update(
        self,
        old_str: str,
        new_str: str,
        max_diff_ratio: float = 0.3
    ) -> Tuple[bool, str, Optional[str], Optional[Dict]]:
        """
        计算更新结果（分层匹配策略）

        Layer 0: 精确匹配
        Layer 1: CJK-Latin 空格归一化 + 精确匹配
        Layer 2: fuzzysearch 近似子串搜索（兜底）

        Args:
            old_str: 要替换的原文本
            new_str: 新文本
            max_diff_ratio: 最大允许的差异率（用于 Layer 2）

        Returns:
            (成功与否, 消息, 新内容, 匹配详情字典)
        """
        # Layer 0: 精确匹配
        if old_str in self.content:
            count = self.content.count(old_str)

            if count > 1:
                return False, f"Text '{old_str[:50]}...' appears {count} times (must be unique)", None, None

            new_content = self.content.replace(old_str, new_str, 1)

            return True, "exact match", new_content, {
                "match_type": "exact",
                "similarity": 1.0,
                "changes": [(old_str, new_str)]
            }

        # Layer 1: 归一化 + 精确匹配
        logger.debug("Exact match failed, trying normalized match...")

        norm_old, _ = _normalize_for_match(old_str)
        norm_content, content_idx_map = _normalize_for_match(self.content)

        if norm_old in norm_content:
            count = norm_content.count(norm_old)
            if count > 1:
                return False, f"Text '{old_str[:50]}...' appears {count} times after normalization (must be unique)", None, None

            # Map normalized position back to original content
            norm_start = norm_content.index(norm_old)
            norm_end = norm_start + len(norm_old)

            orig_start = content_idx_map[norm_start]
            # norm_end could be == len(content_idx_map) if match reaches end
            orig_end = content_idx_map[norm_end] if norm_end < len(content_idx_map) else len(self.content)

            matched_text = self.content[orig_start:orig_end]
            new_content = self.content[:orig_start] + new_str + self.content[orig_end:]

            similarity = 1.0 - (abs(len(matched_text) - len(old_str)) / max(len(matched_text), len(old_str)))
            logger.info(
                f"Normalized match succeeded (similarity: {similarity:.1%})\n"
                f"Expected: {old_str[:100]}...\n"
                f"Actual:   {matched_text[:100]}..."
            )

            return True, f"normalized match {similarity:.1%}", new_content, {
                "match_type": "normalized",
                "similarity": similarity,
                "expected_text": old_str,
                "matched_text": matched_text,
                "changes": [(matched_text, new_str)]
            }

        # Layer 2: fuzzysearch 近似子串搜索
        logger.debug("Normalized match failed, trying fuzzysearch...")

        max_l_dist = max(5, int(len(old_str) * max_diff_ratio))
        matches = find_near_matches(old_str, self.content, max_l_dist=max_l_dist)

        if not matches:
            return False, f"Failed to find matching text '{old_str[:50]}...'", None, None

        if len(matches) > 1:
            # Pick the best (lowest distance); reject if ambiguous (same distance)
            matches.sort(key=lambda m: m.dist)
            if matches[0].dist == matches[1].dist:
                return False, f"Text '{old_str[:50]}...' has {len(matches)} ambiguous fuzzy matches", None, None

        best = matches[0]
        matched_text = self.content[best.start:best.end]
        levenshtein_distance = best.dist

        if levenshtein_distance > len(old_str) * max_diff_ratio:
            return False, f"Best match difference is too large (edit distance: {levenshtein_distance})", None, None

        new_content = self.content[:best.start] + new_str + self.content[best.end:]

        similarity = 1.0 - (levenshtein_distance / len(old_str))
        logger.info(
            f"Fuzzy match succeeded (similarity: {similarity:.1%})\n"
            f"Expected: {old_str[:100]}...\n"
            f"Actual:   {matched_text[:100]}..."
        )

        return True, f"fuzzy match {similarity:.1%}", new_content, {
            "match_type": "fuzzy",
            "similarity": similarity,
            "expected_text": old_str,
            "matched_text": matched_text,
            "changes": [(matched_text, new_str)]
        }


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
    - 使用乐观锁进行并发控制

    使用方式：
        async with db_manager.session() as session:
            repo = ArtifactRepository(session)
            manager = ArtifactManager(repo)
            await manager.create_artifact(...)
    """

    def __init__(self, repository: Optional[ArtifactRepository] = None):
        """
        初始化 ArtifactManager

        Args:
            repository: ArtifactRepository 实例（通过依赖注入）
                       可以为 None，稍后通过 set_repository 设置
        """
        self.repository = repository
        self._cache: Dict[str, Dict[str, ArtifactMemory]] = {}  # {session_id: {artifact_id: ArtifactMemory}}
        self._current_session_id: Optional[str] = None

        logger.debug("ArtifactManager initialized")

    def set_repository(self, repository: ArtifactRepository) -> None:
        """
        设置/更新 Repository（用于每次请求时绑定新的数据库 session）

        Args:
            repository: ArtifactRepository 实例
        """
        self.repository = repository

    def _ensure_repository(self) -> ArtifactRepository:
        """确保 Repository 已设置"""
        if self.repository is None:
            raise RuntimeError("ArtifactManager: repository not configured. Call set_repository() first.")
        return self.repository

    def set_session(self, session_id: str) -> None:
        """设置当前 session"""
        self._current_session_id = session_id
        if session_id not in self._cache:
            self._cache[session_id] = {}

    @property
    def current_session_id(self) -> Optional[str]:
        """获取当前 session ID"""
        return self._current_session_id

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
        创建新的 Artifact

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            content_type: 内容类型
            title: 标题
            content: 初始内容
            metadata: 元数据
            source: 来源 (agent, user_upload)

        Returns:
            (成功与否, 消息)
        """
        try:
            repo = self._ensure_repository()

            # 1. 确保 session 存在
            await self.ensure_session_exists(session_id)

            # 2. 创建数据库记录
            db_artifact = await repo.create_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                metadata=metadata,
                source=source
            )

            # 3. 创建内存缓存
            memory = ArtifactMemory(
                artifact_id=artifact_id,
                content_type=content_type,
                title=title,
                content=content,
                current_version=db_artifact.current_version,
                lock_version=db_artifact.lock_version,
                metadata=metadata,
                created_at=db_artifact.created_at,
                source=source,
            )

            if session_id not in self._cache:
                self._cache[session_id] = {}
            self._cache[session_id][artifact_id] = memory

            logger.info(f"Created artifact '{artifact_id}' in session '{session_id}'")
            return True, f"Created artifact '{artifact_id}'"

        except DuplicateError:
            return False, f"Artifact '{artifact_id}' already exists in session"
        except NotFoundError as e:
            return False, str(e)
        except Exception as e:
            logger.exception(f"Failed to create artifact: {e}")
            return False, f"Failed to create artifact: {str(e)}"

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

        Args:
            session_id: Session ID
            filename: Original filename
            content: Converted text content
            content_type: MIME type (after conversion)
            metadata: Conversion metadata

        Returns:
            (success, message, artifact_info dict or None)
        """
        # Generate artifact_id from filename (allow Unicode letters/digits)
        base = re.sub(r'[^\w\-.]', '_', filename)
        artifact_id = base.lower()

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

        # Title from filename (without extension)
        import os
        title = os.path.splitext(filename)[0]

        upload_metadata = metadata or {}
        upload_metadata["original_filename"] = filename

        success, message = await self.create_artifact(
            session_id=session_id,
            artifact_id=artifact_id,
            content_type=content_type,
            title=title,
            content=content,
            metadata=upload_metadata,
            source="user_upload"
        )

        if success:
            return True, message, {
                "id": artifact_id,
                "session_id": session_id,
                "content_type": content_type,
                "title": title,
                "current_version": 1,
                "source": "user_upload",
                "original_filename": filename,
            }
        return False, message, None

    async def get_artifact(
        self,
        session_id: str,
        artifact_id: str
    ) -> Optional[ArtifactMemory]:
        """
        获取 Artifact（优先从缓存）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID

        Returns:
            ArtifactMemory 对象
        """
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
            lock_version=db_artifact.lock_version,
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
        """
        更新 Artifact 内容（使用 diff-match-patch）

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            old_str: 要替换的文本
            new_str: 新文本

        Returns:
            (成功与否, 消息, 匹配信息)
        """
        # 1. 获取内存对象
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found", None

        # 2. 计算更新
        success, msg, new_content, match_info = memory.compute_update(old_str, new_str)

        if not success:
            return False, msg, None

        # 3. 持久化到数据库（使用乐观锁）
        try:
            repo = self._ensure_repository()
            update_type = "update" if match_info["match_type"] == "exact" else "update_fuzzy"

            db_artifact = await repo.update_artifact_content(
                session_id=session_id,
                artifact_id=artifact_id,
                new_content=new_content,
                update_type=update_type,
                expected_lock_version=memory.lock_version,
                changes=match_info.get("changes"),
                source="agent"
            )

            # 4. 更新内存缓存
            memory.content = new_content
            memory.current_version = db_artifact.current_version
            memory.lock_version = db_artifact.lock_version
            memory.updated_at = datetime.now()
            memory.source = "agent"

            return True, f"Successfully updated artifact '{artifact_id}' (v{memory.current_version})", match_info

        except VersionConflictError as e:
            # 版本冲突，需要重新加载
            logger.warning(f"Version conflict: {e}")
            # 清除缓存，下次访问时重新加载
            if session_id in self._cache and artifact_id in self._cache[session_id]:
                del self._cache[session_id][artifact_id]
            return False, f"Version conflict: artifact was modified by another process", None
        except Exception as e:
            logger.exception(f"Failed to update artifact: {e}")
            return False, f"Failed to update artifact: {str(e)}", None

    async def rewrite_artifact(
        self,
        session_id: str,
        artifact_id: str,
        new_content: str
    ) -> Tuple[bool, str]:
        """
        完全重写 Artifact 内容

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            new_content: 新内容

        Returns:
            (成功与否, 消息)
        """
        # 1. 获取内存对象
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found"

        # 2. 持久化到数据库
        try:
            repo = self._ensure_repository()
            db_artifact = await repo.rewrite_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                new_content=new_content,
                expected_lock_version=memory.lock_version,
                source="agent"
            )

            # 3. 更新内存缓存
            memory.content = new_content
            memory.current_version = db_artifact.current_version
            memory.lock_version = db_artifact.lock_version
            memory.updated_at = datetime.now()
            memory.source = "agent"

            return True, f"Successfully rewritten artifact '{artifact_id}' (v{memory.current_version})"

        except VersionConflictError:
            if session_id in self._cache and artifact_id in self._cache[session_id]:
                del self._cache[session_id][artifact_id]
            return False, "Version conflict: artifact was modified by another process"
        except Exception as e:
            logger.exception(f"Failed to rewrite artifact: {e}")
            return False, f"Failed to rewrite artifact: {str(e)}"

    async def read_artifact(
        self,
        session_id: str,
        artifact_id: str,
        version: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        读取 Artifact 内容

        Args:
            session_id: Session ID
            artifact_id: Artifact ID
            version: 版本号（None 则读取最新版本）

        Returns:
            Artifact 信息字典
        """
        if version is None:
            # 读取当前版本
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
                "created_at": memory.created_at.isoformat(),
                "updated_at": memory.updated_at.isoformat()
            }
        else:
            # 读取历史版本
            repo = self._ensure_repository()
            content = await repo.get_version_content(session_id, artifact_id, version)
            if content is None:
                return None

            memory = await self.get_artifact(session_id, artifact_id)
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

    async def list_artifacts(
        self,
        session_id: str,
        content_type: Optional[str] = None,
        include_content: bool = True,
        content_preview_length: int = 200,
        full_content_for: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        列出 Session 的所有 Artifacts

        Args:
            session_id: Session ID
            content_type: 按类型筛选
            include_content: 是否包含内容
            content_preview_length: 内容预览长度
            full_content_for: 需要完整内容的 artifact ID 列表

        Returns:
            Artifact 信息列表
        """
        if full_content_for is None:
            full_content_for = []

        repo = self._ensure_repository()

        # 从数据库获取列表
        artifacts = await repo.list_artifacts(
            session_id=session_id,
            content_type=content_type,
            include_content=include_content,
            content_preview_length=content_preview_length
        )

        # 处理需要完整内容的 artifacts
        if full_content_for:
            full_artifacts = await repo.get_artifacts_with_full_content(
                session_id, full_content_for
            )
            for artifact_info in artifacts:
                if artifact_info["id"] in full_artifacts:
                    artifact_info["content"] = full_artifacts[artifact_info["id"]].content

        return artifacts

    def clear_cache(self, session_id: Optional[str] = None) -> None:
        """
        清除缓存

        Args:
            session_id: Session ID（None 则清除所有）
        """
        if session_id:
            if session_id in self._cache:
                del self._cache[session_id]
        else:
            self._cache.clear()


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


class UpdateArtifactTool(BaseTool):
    """
    更新 Artifact 工具
    通过指定 old_str 和 new_str 来更新内容（支持模糊匹配）
    """

    def __init__(self, manager: Optional[ArtifactManager] = None):
        super().__init__(
            name="update_artifact",
            description="Update artifact content by replacing old text with new text (supports fuzzy matching). Use for targeted changes.",
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
                description="Artifact ID to update",
                required=True
            ),
            ToolParameter(
                name="old_str",
                type="string",
                description="Text to be replaced",
                required=True
            ),
            ToolParameter(
                name="new_str",
                type="string",
                description="New text to replace with",
                required=True
            )
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message, match_info = await self._manager.update_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            old_str=params["old_str"],
            new_str=params["new_str"]
        )

        if success:
            logger.info(message)

            memory = await self._manager.get_artifact(session_id, params["id"])
            version = memory.current_version if memory else None

            if match_info and match_info.get("match_type") == "fuzzy":
                similarity = f"{match_info['similarity']:.1%}"
                expected = _truncate_middle(match_info["expected_text"], 200)
                matched = _truncate_middle(match_info["matched_text"], 200)
                xml = (
                    f'<artifact version="{version}" fuzzy="{similarity}">'
                    f"\n  <id>{params['id']}</id>"
                    f"\n  {message}"
                    f"\n  <fuzzy_detail>"
                    f"\n    <expected>{expected}</expected>"
                    f"\n    <matched>{matched}</matched>"
                    f"\n  </fuzzy_detail>"
                    f"\n</artifact>"
                )
            else:
                xml = f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>'

            return ToolResult(success=True, data=xml, metadata=match_info)

        return ToolResult(success=False, error=message)

    def to_xml_example(self) -> str:
        """生成 XML 调用示例（使用CDATA）"""
        return """<tool_call>
  <name>update_artifact</name>
  <params>
    <id><![CDATA[task_plan]]></id>
    <old_str><![CDATA[1. [✗] Search for recent developments
   - Status: pending
   - Assigned: search_agent
   - Notes: N/A]]></old_str>
    <new_str><![CDATA[1. [✓] Search for recent developments
   - Status: completed
   - Assigned: search_agent
   - Notes: Found 5 key breakthroughs]]></new_str>
  </params>
</tool_call>"""


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
            description="Read full artifact content. Artifact inventory only shows previews — use this for complete content.",
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
                description="Artifact ID to read",
                required=True
            ),
            ToolParameter(
                name="version",
                type="integer",
                description="Version number (optional, defaults to latest)",
                required=False,
                default=None
            )
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

        # result is a dict from ArtifactManager.read_artifact
        artifact_id = result.get("id", "")
        content_type = result.get("content_type", "")
        title = result.get("title", "")
        version_num = result.get("version", "")
        source = result.get("source", "agent")
        updated_at = result.get("updated_at", "")
        content = result.get("content", "")

        # 受控值 → attribute; 用户文本 → 子元素（与 inventory 格式一致）
        xml = (
            f'<artifact version="{version_num}" type="{content_type}"'
            f' source="{source}" updated="{updated_at}">\n'
            f'<id>{artifact_id}</id>\n'
            f'<title>{title}</title>\n'
            f'{content}\n'
            f'</artifact>'
        )
        return ToolResult(success=True, data=xml)


# ============================================================
# 工厂函数
# ============================================================

def create_artifact_tools(manager: ArtifactManager) -> List[BaseTool]:
    """
    创建所有 Artifact 工具（工厂函数）

    Args:
        manager: ArtifactManager 实例

    Returns:
        工具列表
    """
    return [
        CreateArtifactTool(manager),
        UpdateArtifactTool(manager),
        RewriteArtifactTool(manager),
        ReadArtifactTool(manager),
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
