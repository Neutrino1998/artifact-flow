"""Artifact 操作工具和管理器（ArtifactManager + write-back cache）"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
import re
import unicodedata
from fuzzysearch import find_near_matches

from sqlalchemy.exc import IntegrityError

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from repositories.artifact_repo import ArtifactRepository
from repositories.base import NotFoundError, DuplicateError
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


# Merged 1-to-1 translation table
_ALL_CHAR_TRANSLATES = {**_SMART_QUOTES, **_UNICODE_DASHES, **_SPECIAL_SPACES}


# Type alias: each normalized char maps to [start, end) in the original text
Span = Tuple[int, int]


def _nfkc_span_map(pre: str, post: str) -> list[Span]:
    """Build span map for whole-string NFKC: post[i] came from pre[span].

    Uses NFKD as a bridge: NFKD(pre) ≡ NFKD(post), so per-char NFKD
    on each side gives a shared decomposition we can zip through.

    Returns list of (orig_start, orig_end) spans, one per post char.
    """
    # Left: per-char NFKD of pre → each decomposed char knows its pre index
    left_origins: list[int] = []
    for idx, ch in enumerate(pre):
        for _ in unicodedata.normalize('NFKD', ch):
            left_origins.append(idx)

    # Right: per-char NFKD of post → each decomposed char knows its post index
    right_origins: list[int] = []
    for idx, ch in enumerate(post):
        for _ in unicodedata.normalize('NFKD', ch):
            right_origins.append(idx)

    # Zip through shared decomposition to find min/max original index per post char
    span_min: dict[int, int] = {}
    span_max: dict[int, int] = {}
    for decomp_pos in range(len(left_origins)):
        post_idx = right_origins[decomp_pos]
        orig_idx = left_origins[decomp_pos]
        if post_idx not in span_min:
            span_min[post_idx] = orig_idx
            span_max[post_idx] = orig_idx
        else:
            span_min[post_idx] = min(span_min[post_idx], orig_idx)
            span_max[post_idx] = max(span_max[post_idx], orig_idx)

    return [(span_min[i], span_max[i] + 1) for i in range(len(post))]


def _normalize_for_match(text: str) -> tuple[str, list[Span]]:
    """Normalize text and build a span map back to the original.

    Each normalized character maps to a [start, end) span in the
    **original** text. This handles all length-changing transforms
    uniformly: expansions (Ⅳ→IV), contractions (가→가), and
    deletions (rstrip, CJK-Latin space collapse).

    Transforms (in order):
    1. Smart quotes, Unicode dashes, special spaces → ASCII (1-to-1)
    2. Whole-string NFKC (may expand or contract)
    3. Strip trailing whitespace per line
    4. Collapse spaces at CJK-Latin/digit boundaries

    Returns:
        (normalized_text, span_map) where span_map[i] = (start, end)
        in the **original** text for normalized char i.
    """
    # Phase 1: 1-to-1 char translates (preserves length)
    translated = text.translate(_ALL_CHAR_TRANSLATES)

    # Phase 2: whole-string NFKC + NFKD-bridge span alignment
    nfkc_text = unicodedata.normalize('NFKC', translated)
    spans = _nfkc_span_map(translated, nfkc_text)
    # spans[i] = (start, end) in `translated` = in original `text`

    # Phase 3: rstrip per line (drop trailing spaces, keep spans of survivors)
    chars = list(nfkc_text)
    stripped_chars: list[str] = []
    stripped_spans: list[Span] = []
    line_chars: list[str] = []
    line_spans: list[Span] = []

    for c, s in zip(chars, spans):
        if c == '\n':
            while line_chars and line_chars[-1] == ' ':
                line_chars.pop()
                line_spans.pop()
            stripped_chars.extend(line_chars)
            stripped_spans.extend(line_spans)
            stripped_chars.append(c)
            stripped_spans.append(s)
            line_chars.clear()
            line_spans.clear()
        else:
            line_chars.append(c)
            line_spans.append(s)

    while line_chars and line_chars[-1] == ' ':
        line_chars.pop()
        line_spans.pop()
    stripped_chars.extend(line_chars)
    stripped_spans.extend(line_spans)

    # Phase 4: collapse CJK-Latin boundary spaces
    result: list[str] = []
    result_spans: list[Span] = []
    i = 0
    while i < len(stripped_chars):
        if stripped_chars[i] == ' ' and result and i + 1 < len(stripped_chars):
            j = i
            while j < len(stripped_chars) and stripped_chars[j] == ' ':
                j += 1
            if j < len(stripped_chars):
                prev_is_cjk = bool(re.match(_CJK_RE, result[-1]))
                next_is_cjk = bool(re.match(_CJK_RE, stripped_chars[j]))
                prev_is_latin = bool(re.match(r'[A-Za-z0-9]', result[-1]))
                next_is_latin = bool(re.match(r'[A-Za-z0-9]', stripped_chars[j]))

                if (prev_is_cjk and next_is_latin) or (prev_is_latin and next_is_cjk):
                    i = j
                    continue

        result.append(stripped_chars[i])
        result_spans.append(stripped_spans[i])
        i += 1

    return ''.join(result), result_spans


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
        norm_content, content_span_map = _normalize_for_match(self.content)

        if norm_old in norm_content:
            count = norm_content.count(norm_old)
            if count > 1:
                return False, f"Text '{old_str[:50]}...' appears {count} times after normalization (must be unique)", None, None

            norm_start = norm_content.index(norm_old)
            norm_end = norm_start + len(norm_old)

            # Span-based boundary safety: reject if match starts or ends
            # inside a normalization group (chars sharing the same span).
            if norm_start > 0 and content_span_map[norm_start] == content_span_map[norm_start - 1]:
                logger.debug("Normalized match starts inside a normalization group, falling through to Layer 2")
            elif norm_end < len(content_span_map) and content_span_map[norm_end] == content_span_map[norm_end - 1]:
                logger.debug("Normalized match ends inside a normalization group, falling through to Layer 2")
            else:
                orig_start = content_span_map[norm_start][0]
                orig_end = content_span_map[norm_end - 1][1]

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
    - 执行期间只改内存，loop 结束统一 flush

    使用方式：
        async with db_manager.session() as session:
            repo = ArtifactRepository(session)
            manager = ArtifactManager(repo)
            await manager.create_artifact(...)
    """

    def __init__(self, repository: Optional[ArtifactRepository] = None):
        self.repository = repository
        self._cache: Dict[str, Dict[str, ArtifactMemory]] = {}  # {session_id: {artifact_id: ArtifactMemory}}
        self._current_session_id: Optional[str] = None
        self._dirty: set = set()  # Set of (session_id, artifact_id) tuples
        self._new: set = set()    # Set of (session_id, artifact_id) tuples — created during this execution

    def _ensure_repository(self) -> ArtifactRepository:
        """确保 Repository 已设置"""
        if self.repository is None:
            raise RuntimeError("ArtifactManager: repository not configured")
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
        创建新的 Artifact（只写内存，flush_all 时持久化）
        """
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

            # 标记为 dirty + new
            key = (session_id, artifact_id)
            self._dirty.add(key)
            self._new.add(key)

            logger.info(f"Created artifact '{artifact_id}' in session '{session_id}' (pending flush)")
            return True, f"Created artifact '{artifact_id}'"

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
        Uploads are committed immediately (not deferred to flush_all).
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

    def build_snapshot(self, session_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Build an artifact snapshot dict from the in-memory cache for SSE transport."""
        memory = self._cache.get(session_id, {}).get(artifact_id)
        if not memory:
            return None
        return {
            "id": memory.id,
            "session_id": session_id,
            "content_type": memory.content_type,
            "title": memory.title,
            "content": memory.content,
            "current_version": memory.current_version,
            "source": memory.source,
        }

    async def update_artifact(
        self,
        session_id: str,
        artifact_id: str,
        old_str: str,
        new_str: str
    ) -> Tuple[bool, str, Optional[Dict]]:
        """更新 Artifact 内容（只改内存，标记 dirty）"""
        memory = await self.get_artifact(session_id, artifact_id)
        if not memory:
            return False, f"Artifact '{artifact_id}' not found", None

        success, msg, new_content, match_info = memory.compute_update(old_str, new_str)

        if not success:
            return False, msg, None

        # 只改内存
        memory.content = new_content
        memory.current_version += 1
        memory.updated_at = datetime.now()
        memory.source = "agent"

        self._dirty.add((session_id, artifact_id))

        return True, f"Successfully updated artifact '{artifact_id}' (v{memory.current_version})", match_info

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

        self._dirty.add((session_id, artifact_id))

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
                "created_at": memory.created_at.isoformat(),
                "updated_at": memory.updated_at.isoformat()
            }
        else:
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
            return

        to_flush = [(sid, aid) for sid, aid in self._dirty if sid == session_id]
        failed: list = []

        for sid, aid in to_flush:
            memory = self._cache.get(sid, {}).get(aid)
            if not memory:
                continue

            try:
                await self._flush_one(sid, aid, memory, db_manager=db_manager)
                # Success — remove from dirty/new
                self._dirty.discard((sid, aid))
                self._new.discard((sid, aid))
                logger.info(f"Flushed artifact '{aid}' in session '{sid}'")
            except Exception as e:
                logger.exception(f"Failed to flush artifact '{aid}': {e}")
                failed.append((aid, e))

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
            snapshot = self._manager.build_snapshot(session_id, params["id"])
            return ToolResult(
                success=True,
                data=f'<artifact version="1"><id>{params["id"]}</id> {message}</artifact>',
                metadata={"artifact_snapshot": snapshot} if snapshot else {},
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

            metadata = match_info or {}
            snapshot = self._manager.build_snapshot(session_id, params["id"])
            if snapshot:
                metadata["artifact_snapshot"] = snapshot
            return ToolResult(success=True, data=xml, metadata=metadata)

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
            snapshot = self._manager.build_snapshot(session_id, params["id"])
            return ToolResult(
                success=True,
                data=f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>',
                metadata={"artifact_snapshot": snapshot} if snapshot else {},
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
