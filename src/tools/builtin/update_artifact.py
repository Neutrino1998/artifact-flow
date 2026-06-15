"""update_artifact 工具与匹配算法（Layer 0/1/2 一体）。

* Layer 0 — 精确子串匹配（``count == 1`` 严格唯一）
* Layer 1 — 归一化（智能引号 / Unicode 短横 / NFKC / CJK-Latin 空格折叠 /
  行尾空格剥离）后精确匹配，span map 回原文；边界落入归一化组内则拒
* Layer 2 — 稀有 shingle 锚定 + RapidFuzz 有界 Levenshtein 校验
  （详见 ``find_fuzzy_match`` docstring）

不变量：
* ≥2 候选一律响亮失败，与 Layer 0/1 严格唯一性对齐，不替模型做隐式选择
* Layer 2 ``fuzzy_stats``（仅 ``old_str`` 的 sha256，不含原文）在成功 /
  bail 路径都写出，经 ``ToolResult.metadata`` 透传到 MessageEvent
* ``MAX_UNIQUE_CENTERS``（静态 budget）+ ``MAX_FUZZY_WALL_CLOCK_MS``
  （动态 deadline，检查下沉到内层循环）双重收口

依赖方向：本模块 → ``artifact_ops`` 仅 ``TYPE_CHECKING``；反向通过
``artifact_ops.create_artifact_tools`` 工厂局部 import，包级无环。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from rapidfuzz.distance import Levenshtein

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from utils.logger import get_logger

if TYPE_CHECKING:
    from tools.builtin.artifact_service import ArtifactService

logger = get_logger("ArtifactFlow")


# ============================================================
# Layer 1 — 归一化辅助(从 artifact_ops 搬出,无其它消费者)
# ============================================================

# CJK Unicode ranges for normalization
_CJK_RE = (
    r'[⺀-⿟'   # CJK Radicals
    r'　-〿'     # CJK Symbols & Punctuation
    r'぀-ゟ'     # Hiragana
    r'゠-ヿ'     # Katakana
    r'㐀-䶿'     # CJK Unified Ext A
    r'一-鿿'     # CJK Unified
    r'豈-﫿'     # CJK Compat Ideographs
    r'︰-﹏'     # CJK Compat Forms
    r'＀-￯'     # Halfwidth/Fullwidth Forms
    r'\U00020000-\U0002a6df'  # CJK Unified Ext B
    r']'
)

# Smart quotes → ASCII
_SMART_QUOTES = str.maketrans({
    '‘': "'",   # '
    '’': "'",   # '
    '‚': "'",   # ‚
    '“': '"',   # "
    '”': '"',   # "
    '„': '"',   # „
    '‹': "'",   # ‹
    '›': "'",   # ›
    '«': '"',   # «
    '»': '"',   # »
})

# Unicode dashes → ASCII hyphen
_UNICODE_DASHES = str.maketrans({
    '‒': '-',   # figure dash
    '–': '-',   # en dash –
    '—': '-',   # em dash —
    '―': '-',   # horizontal bar ―
    '−': '-',   # minus sign −
    '﹘': '-',   # small em dash ﹘
    '﹣': '-',   # small hyphen-minus ﹣
    '－': '-',   # fullwidth hyphen-minus －
})

# Special whitespace → regular space
_SPECIAL_SPACES = str.maketrans({
    ' ': ' ',   # non-breaking space
    ' ': ' ',   # en quad
    ' ': ' ',   # em quad
    ' ': ' ',   # en space
    ' ': ' ',   # em space
    ' ': ' ',   # three-per-em space
    ' ': ' ',   # four-per-em space
    ' ': ' ',   # six-per-em space
    ' ': ' ',   # figure space
    ' ': ' ',   # punctuation space
    ' ': ' ',   # thin space
    ' ': ' ',   # hair space
    ' ': ' ',   # narrow no-break space
    ' ': ' ',   # medium mathematical space
    '　': ' ',   # ideographic space
})

# Merged 1-to-1 translation table
_ALL_CHAR_TRANSLATES = {**_SMART_QUOTES, **_UNICODE_DASHES, **_SPECIAL_SPACES}


# Type alias: each normalized char maps to [start, end) in the original text
Span = Tuple[int, int]


def _nfkc_span_map(pre: str, post: str) -> list[Span]:
    """Build span map for whole-string NFKC: post[i] came from pre[span]."""
    left_origins: list[int] = []
    for idx, ch in enumerate(pre):
        for _ in unicodedata.normalize('NFKD', ch):
            left_origins.append(idx)

    right_origins: list[int] = []
    for idx, ch in enumerate(post):
        for _ in unicodedata.normalize('NFKD', ch):
            right_origins.append(idx)

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
    **original** text. Handles expansions (Ⅳ→IV), contractions (가→가),
    and deletions (rstrip, CJK-Latin space collapse) uniformly.

    Transforms (in order):
    1. Smart quotes, Unicode dashes, special spaces → ASCII (1-to-1)
    2. Whole-string NFKC (may expand or contract)
    3. Strip trailing whitespace per line
    4. Collapse spaces at CJK-Latin/digit boundaries
    """
    translated = text.translate(_ALL_CHAR_TRANSLATES)

    nfkc_text = unicodedata.normalize('NFKC', translated)
    spans = _nfkc_span_map(translated, nfkc_text)

    # Phase 3: rstrip per line
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
# Layer 2 — dataclasses
# ============================================================


@dataclass
class FuzzyMatch:
    """Layer 2 successful match."""
    start: int
    end: int
    distance: int
    similarity: float        # 1 - distance / max(m, end - start)
    matched_text: str
    fuzzy_stats: Dict[str, Any]


@dataclass
class FuzzyBail:
    """Layer 2 bailed with a structured reason."""
    outcome: str             # bail_low_entropy | bail_no_anchor | bail_budget
                             #   | bail_deadline | bail_ambiguous | bail_no_window
    message: str             # hint shown to the LLM
    fuzzy_stats: Optional[Dict[str, Any]] = None


FuzzyResult = Union[FuzzyMatch, FuzzyBail]


@dataclass
class MatchInfo:
    """Unified result of ``compute_update`` covering Layer 0/1/2."""
    success: bool
    message: str
    new_content: Optional[str] = None
    match_type: Optional[str] = None       # exact | normalized | fuzzy
    similarity: Optional[float] = None
    expected_text: Optional[str] = None    # only for normalized / fuzzy success
    matched_text: Optional[str] = None     # only for normalized / fuzzy success
    changes: Optional[List[Tuple[str, str]]] = None
    # Present only when Layer 2 ran (success OR bail). Identity is preserved
    # by ``UpdateArtifactTool`` so MessageEvent metadata == the same dict.
    fuzzy_stats: Optional[Dict[str, Any]] = None
    # 命中 span,坐标在**原始 content**(替换前)里:replace [offset, offset+deleted_len)
    # with new_str。仅 success 时填。这是 ARTIFACT_UPDATED 权威 delta 的来源——前端
    # 无法从工具 params 反推(模糊匹配命中的真实位置只有这里知道)。三层都填:
    # Layer 0 exact = content.index(old_str)(count==1 唯一);Layer 1 = 归一化 span
    # map 回原文的 orig_start;Layer 2 = fuzzy 命中的 start。
    offset: Optional[int] = None
    deleted_len: Optional[int] = None


# ============================================================
# Layer 2 — anchor-bounded fuzzy match (v6)
# ============================================================

# Shingles consisting entirely of "low-info" chars are filtered out: tables /
# templates / number columns generate noise that exhausts the center budget
# without ever carrying real positional signal. The set is deliberately
# conservative — anything outside is kept as a candidate anchor.
_LOW_INFO_CHARS = frozenset(" \t|-_=.,;:0123456789")


def _is_low_info_shingle(s: str) -> bool:
    return all(ch in _LOW_INFO_CHARS for ch in s)


def _hash_old_str(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _build_stats(
    *,
    m: int,
    n: int,
    k: int,
    L: int,
    old_hash: str,
    rare_shingles: int = 0,
    raw_centers: int = 0,
    unique_centers: int = 0,
    verify_calls: int = 0,
    elapsed_ms: int = 0,
    outcome: str,
    distance: Optional[int] = None,
    similarity_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Build the canonical ``fuzzy_stats`` payload.

    Field set is part of the contract with ``scripts/observability_report.py``;
    keep it stable. ``old_str`` itself is **never** included — only the hash,
    so cross-event dedup / clustering stays possible without leaking content.
    """
    stats: Dict[str, Any] = {
        "m": m, "n": n, "k": k, "L": L,
        "rare_shingles": rare_shingles,
        "raw_centers": raw_centers,
        "unique_centers": unique_centers,
        "verify_calls": verify_calls,
        "elapsed_ms": elapsed_ms,
        "outcome": outcome,
        "old_str_hash": old_hash,
    }
    if distance is not None:
        stats["distance"] = distance
    if similarity_pct is not None:
        stats["similarity_pct"] = similarity_pct
    return stats


def find_fuzzy_match(old_str: str, content: str) -> FuzzyResult:
    """Anchor-bounded fuzzy match. Worst-case cost is self-imposed via
    ``MAX_UNIQUE_CENTERS`` (static budget) + ``MAX_FUZZY_WALL_CLOCK_MS``
    (dynamic deadline), not a function of document entropy.

    Pipeline:

    1. Slice ``old_str`` into length-``L`` shingles. ``L`` is derived from
       pigeonhole ``L ≤ m // (k+1)``; bail loudly when ``L`` falls below
       ``ANCHOR_MIN_USABLE_LEN`` (no usable anchor length exists).
    2. Overlapping scan of ``content`` records rare-shingle positions.
       ``str.count`` is unsafe here — non-overlapping count undercounts
       low-entropy spans and lets common shingles slip through.
    3. Expand ``q - p`` centers for **every** ``(s, p, q)`` rare triple
       (no top-N gating — pigeonhole does not promise the surviving
       shingle ranks first by rarity). Dedupe within ``allowed_dist``,
       bail if the deduped set exceeds ``MAX_UNIQUE_CENTERS``.
    4. For each surviving center, enumerate ``(2k+1)²`` start/end offsets
       and run ``Levenshtein.distance(..., score_cutoff=k)``. Deadline
       check sits in the **inner** loop — a single center can otherwise
       burn the entire wall-clock budget by itself.
    5. Merge near-identical regions (``|Δstart| ≤ k`` AND ``|Δend| ≤ k``);
       any remaining ambiguity → ``bail_ambiguous`` (Layer 0/1 are strictly
       unique, Layer 2 must not be looser).

    Returns ``FuzzyMatch`` on success, ``FuzzyBail`` on any failure path.
    ``fuzzy_stats`` is attached to both for the observability pipeline.
    """
    m = len(old_str)
    n = len(content)
    old_hash = _hash_old_str(old_str)

    # ---- Hard input-size cap (must precede Step 1) ----
    # Step 1-3 are pure Python (shingle dict build, raw_centers expand,
    # sort+dedupe) and run BEFORE the wall-clock deadline guard can fire.
    # At m ≈ 400K they alone exceed MAX_FUZZY_WALL_CLOCK_MS. Beyond the
    # cap, the right tool is rewrite_artifact, not best-effort fuzzy.
    if m > config.MAX_FUZZY_OLD_STR_LEN:
        return FuzzyBail(
            outcome="bail_budget",
            message=(
                f"old_str 长度 {m} 超出 Layer 2 上界 "
                f"{config.MAX_FUZZY_OLD_STR_LEN}:"
                "请缩小 old_str 范围,或改用 `rewrite_artifact`"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=0, L=0, old_hash=old_hash,
                outcome="bail_budget",
            ),
        )

    # ---- Preamble: derive effective parameters ----
    allowed_dist = min(
        config.FUZZY_MAX_L_DIST,
        max(1, int(m * config.FUZZY_MAX_RATIO)),
    )
    # Pigeonhole: any two strings within Lev k share a substring of length
    # at least m // (k+1). Going above that length silently drops legal
    # matches (false negatives); going below ANCHOR_MIN_USABLE_LEN means
    # the document is too low-entropy / k is too wide vs m to anchor at all.
    L = min(config.ANCHOR_SHINGLE_LEN, m // (allowed_dist + 1))

    if L < config.ANCHOR_MIN_USABLE_LEN:
        return FuzzyBail(
            outcome="bail_low_entropy",
            message=(
                "old_str 太短或与目标差异过大,无法可靠定位:"
                "请提供更长 / 更独特的上下文"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                outcome="bail_low_entropy",
            ),
        )

    started_at = monotonic()

    # ---- Step 1: shingle the pattern ----
    old_pos: Dict[str, List[int]] = defaultdict(list)
    for p in range(m - L + 1):
        s = old_str[p:p + L]
        if _is_low_info_shingle(s):
            continue
        old_pos[s].append(p)

    if not old_pos:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_anchor",
            message=(
                "old_str 全部由低信息字符(空白 / 数字 / 标点)构成,无法定位:"
                "请提供包含具体词汇的上下文,或改用 `rewrite_artifact`"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                elapsed_ms=elapsed_ms, outcome="bail_no_anchor",
            ),
        )

    # ---- Step 2: rare-shingle scan over content (overlapping) ----
    positions: Dict[str, List[int]] = defaultdict(list)
    common: set = set()
    for i in range(n - L + 1):
        s = content[i:i + L]
        if s in old_pos and s not in common:
            positions[s].append(i)
            if len(positions[s]) > config.ANCHOR_MAX_OCCURRENCES:
                common.add(s)
                del positions[s]

    rare_count = len(positions)
    if rare_count == 0:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_anchor",
            message=(
                "old_str 太重复或文档已大幅漂移:"
                "请重新 Read 后提供更独特的上下文,或改用 `rewrite_artifact`"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                rare_shingles=0, elapsed_ms=elapsed_ms, outcome="bail_no_anchor",
            ),
        )

    # ---- Step 3: full center expansion + dedupe + static budget ----
    raw_centers: List[int] = []
    for s, q_list in positions.items():
        for p in old_pos[s]:
            for q in q_list:
                raw_centers.append(q - p)

    # Greedy chain-merge after sort: keep first center, skip any subsequent
    # center within `allowed_dist` of the last kept rep. Step 4's (2k+1)²
    # offset enumeration recovers any sub-k drift.
    raw_centers.sort()
    unique_centers: List[int] = []
    for c in raw_centers:
        if not unique_centers or (c - unique_centers[-1]) > allowed_dist:
            unique_centers.append(c)

    if len(unique_centers) > config.MAX_UNIQUE_CENTERS:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        # Loud bail rather than truncate: truncation drops centers in an
        # input-dependent order and can silently lose the real match.
        return FuzzyBail(
            outcome="bail_budget",
            message=(
                "old_str 在文档中触发过多候选对齐:"
                "请提供更独特的上下文"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers),
                elapsed_ms=elapsed_ms, outcome="bail_budget",
            ),
        )

    # ---- Step 4: bounded verification + wall-clock deadline ----
    deadline = started_at + config.MAX_FUZZY_WALL_CLOCK_MS / 1000
    k = allowed_dist
    verify_calls = 0
    matches: List[Tuple[int, int, int]] = []  # (distance, ms, me)
    bailed_deadline = False
    # True iff ANY center had ≥2 distinct (ms, me) tied at its best d, OR a
    # cross-center merge sees two same-d alignments fall into one region.
    # Either case is a genuine span ambiguity — different (ms, me) means
    # different replaced text and different new_content. Aligns with Layer
    # 0/1's strict count==1; Layer 2 must not be looser.
    span_tied = False

    for center_start in unique_centers:
        if bailed_deadline:
            break
        center_end = center_start + m
        best: Optional[Tuple[int, int, int]] = None
        # Per-center tie flag — must reset whenever a strictly lower best is
        # found, otherwise an earlier tie at d=5 would falsely poison a later
        # uncontested d=1 match.
        tied_at_best = False
        for ds in range(-k, k + 1):
            if bailed_deadline:
                break
            for de in range(-k, k + 1):
                # The deadline check MUST be inside the inner loop: a single
                # center has (2k+1)² candidates — k=16 gives 1089 distance
                # calls, easily enough to overshoot a 500 ms budget alone.
                if monotonic() > deadline:
                    bailed_deadline = True
                    break
                ms = max(0, center_start + ds)
                me = min(n, center_end + de)
                if me - ms <= 0:
                    continue
                if abs((me - ms) - m) > k:
                    continue
                d = Levenshtein.distance(old_str, content[ms:me], score_cutoff=k)
                verify_calls += 1
                # RapidFuzz contract (verified against 3.x): when distance
                # exceeds score_cutoff, it returns `score_cutoff + 1`, NOT
                # None. The `<= k` guard is load-bearing — drop it and every
                # over-cutoff candidate enters `matches` ranked by k+1.
                if d > k:
                    continue
                if best is None or d < best[0]:
                    best = (d, ms, me)
                    tied_at_best = False
                elif d == best[0] and (ms, me) != (best[1], best[2]):
                    tied_at_best = True
        if best is not None:
            matches.append(best)
            if tied_at_best:
                span_tied = True

    if bailed_deadline:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        # Discard ALL partial state (including any matches accumulated so far):
        # we can't tell whether un-scanned centers would beat them. Logging at
        # WARN so observability_report flags the threshold for tuning.
        logger.warning(
            "Fuzzy match deadline exceeded (verify_calls=%d m=%d n=%d k=%d L=%d "
            "unique_centers=%d) — bailing loudly",
            verify_calls, m, n, k, L, len(unique_centers),
        )
        return FuzzyBail(
            outcome="bail_deadline",
            message=(
                "old_str 在文档中触发过多候选对齐:"
                "请提供更独特的上下文"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers),
                verify_calls=verify_calls, elapsed_ms=elapsed_ms,
                outcome="bail_deadline",
            ),
        )

    if not matches:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_window",
            message=(
                "未找到满足相似度的窗口:"
                "请重新 Read 后提供更独特的上下文,或改用 `rewrite_artifact`"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers),
                verify_calls=verify_calls, elapsed_ms=elapsed_ms,
                outcome="bail_no_window",
            ),
        )

    # ---- Step 5: region dedupe + uniqueness check ----
    # Group by approximate (ms, me) — within `k` on both endpoints is "same
    # region". Threshold uses k (not L/2) because real edits ≈ k/2 chars will
    # let multi-anchor reconstructions disagree by 2-3 chars on each endpoint.
    matches.sort(key=lambda mt: (mt[1], mt[2]))
    regions: List[Tuple[int, int, int]] = []
    for d, ms, me in matches:
        merged = False
        for idx, (rd, rms, rme) in enumerate(regions):
            if abs(ms - rms) <= k and abs(me - rme) <= k:
                if d < rd:
                    regions[idx] = (d, ms, me)
                elif d == rd and (ms, me) != (rms, rme):
                    # Same region, same distance, different (ms, me) — the
                    # k-threshold merge would silently pick whichever sorted
                    # first. Flag so we bail loudly below.
                    span_tied = True
                merged = True
                break
        if not merged:
            regions.append((d, ms, me))

    elapsed_ms = int((monotonic() - started_at) * 1000)

    if span_tied or len(regions) >= 2:
        return FuzzyBail(
            outcome="bail_ambiguous",
            message=(
                "old_str 在文档中有多个候选位置:"
                "请扩展上下文使其唯一"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers),
                verify_calls=verify_calls, elapsed_ms=elapsed_ms,
                outcome="bail_ambiguous",
            ),
        )

    best_dist, best_ms, best_me = regions[0]
    matched_text = content[best_ms:best_me]
    similarity = 1.0 - (best_dist / max(m, best_me - best_ms))
    similarity_pct = round(similarity * 100, 1)

    return FuzzyMatch(
        start=best_ms,
        end=best_me,
        distance=best_dist,
        similarity=similarity,
        matched_text=matched_text,
        fuzzy_stats=_build_stats(
            m=m, n=n, k=k, L=L, old_hash=old_hash,
            rare_shingles=rare_count, raw_centers=len(raw_centers),
            unique_centers=len(unique_centers),
            verify_calls=verify_calls, elapsed_ms=elapsed_ms,
            outcome="matched",
            distance=best_dist, similarity_pct=similarity_pct,
        ),
    )


# ============================================================
# Layer 0/1/2 dispatcher
# ============================================================


def compute_update(content: str, old_str: str, new_str: str) -> MatchInfo:
    """Layer 0 exact → Layer 1 normalized → Layer 2 fuzzy dispatcher.

    Free function (not a method on ``ArtifactMemory``) so the matching algo
    stays out of the artifact data model and tests can exercise it without
    manager / session scaffolding.
    """
    # ---- Layer 0: exact substring match ----
    if old_str in content:
        count = content.count(old_str)
        if count > 1:
            return MatchInfo(
                success=False,
                message=f"Text '{old_str[:50]}...' appears {count} times (must be unique)",
            )
        offset = content.index(old_str)  # count==1 → 唯一命中
        new_content = content.replace(old_str, new_str, 1)
        return MatchInfo(
            success=True,
            message="exact match",
            new_content=new_content,
            match_type="exact",
            similarity=1.0,
            changes=[(old_str, new_str)],
            offset=offset,
            deleted_len=len(old_str),
        )

    # ---- Layer 1: normalized exact match ----
    logger.debug("Exact match failed, trying normalized match...")

    norm_old, _ = _normalize_for_match(old_str)
    norm_content, content_span_map = _normalize_for_match(content)

    if norm_old in norm_content:
        count = norm_content.count(norm_old)
        if count > 1:
            return MatchInfo(
                success=False,
                message=(
                    f"Text '{old_str[:50]}...' appears {count} times after "
                    f"normalization (must be unique)"
                ),
            )

        norm_start = norm_content.index(norm_old)
        norm_end = norm_start + len(norm_old)

        # Reject if match starts / ends mid normalization-group
        starts_mid = (
            norm_start > 0
            and content_span_map[norm_start] == content_span_map[norm_start - 1]
        )
        ends_mid = (
            norm_end < len(content_span_map)
            and content_span_map[norm_end] == content_span_map[norm_end - 1]
        )
        if not (starts_mid or ends_mid):
            orig_start = content_span_map[norm_start][0]
            orig_end = content_span_map[norm_end - 1][1]

            matched_text = content[orig_start:orig_end]
            new_content = content[:orig_start] + new_str + content[orig_end:]

            similarity = 1.0 - (
                abs(len(matched_text) - len(old_str))
                / max(len(matched_text), len(old_str))
            )
            logger.info(
                "Normalized match succeeded (similarity: %.1f%%)", similarity * 100,
            )

            return MatchInfo(
                success=True,
                message=f"normalized match {similarity:.1%}",
                new_content=new_content,
                match_type="normalized",
                similarity=similarity,
                expected_text=old_str,
                matched_text=matched_text,
                changes=[(matched_text, new_str)],
                offset=orig_start,
                deleted_len=orig_end - orig_start,
            )
        logger.debug("Normalized match boundary fell inside a normalization group, falling through to Layer 2")

    # ---- Layer 2: anchor-bounded fuzzy match ----
    logger.debug("Normalized match failed, trying anchor-bounded fuzzy match...")

    fuzzy_result = find_fuzzy_match(old_str, content)

    if isinstance(fuzzy_result, FuzzyBail):
        return MatchInfo(
            success=False,
            message=fuzzy_result.message,
            fuzzy_stats=fuzzy_result.fuzzy_stats,
        )

    # Success
    new_content = content[:fuzzy_result.start] + new_str + content[fuzzy_result.end:]
    logger.info(
        "Fuzzy match succeeded (similarity: %.1f%%, distance=%d)",
        fuzzy_result.similarity * 100, fuzzy_result.distance,
    )
    return MatchInfo(
        success=True,
        message=f"fuzzy match {fuzzy_result.similarity:.1%}",
        new_content=new_content,
        match_type="fuzzy",
        similarity=fuzzy_result.similarity,
        expected_text=old_str,
        matched_text=fuzzy_result.matched_text,
        changes=[(fuzzy_result.matched_text, new_str)],
        fuzzy_stats=fuzzy_result.fuzzy_stats,
        offset=fuzzy_result.start,
        deleted_len=fuzzy_result.end - fuzzy_result.start,
    )


# ============================================================
# Tool — XML 输出与 metadata 透传
# ============================================================


def _truncate_middle(text: str, max_len: int = 200) -> str:
    """Truncate long text keeping head and tail with '...' in between."""
    if len(text) <= max_len:
        return text
    half = (max_len - 5) // 2  # 5 chars for "\n...\n"
    return text[:half] + "\n...\n" + text[-half:]


class UpdateArtifactTool(BaseTool):
    """Targeted text replacement with three-layer matching.

    XML output mirrors the layer that hit:

    * exact      → ``<artifact version=...>``
    * normalized → adds ``normalized="X%"`` + ``<normalize_detail>`` block
                   (expected vs matched). Same shape as the fuzzy branch so
                   either layer surfaces a structured diff to the model.
    * fuzzy      → ``fuzzy="X%"`` + ``<fuzzy_detail>`` block

    ``ToolResult.metadata`` carries the manager's match info verbatim
    (including ``fuzzy_stats`` when Layer 2 ran). Identity is preserved
    — no field copy / rewrap — so the engine's ``tool_complete`` event
    inherits the same dict and downstream analytics can pull it back via
    ``data->'metadata'->'fuzzy_stats'``.
    """

    def __init__(self, service: Optional["ArtifactService"] = None):
        super().__init__(
            name="update_artifact",
            description=(
                "Update artifact content by replacing old text with new text "
                "(supports fuzzy matching). Use for small, targeted edits — make "
                "several small old_str/new_str replacements rather than one large one."
            ),
            permission=ToolPermission.AUTO,
        )
        self._service = service

    def set_service(self, service: "ArtifactService") -> None:
        self._service = service

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="string",
                description="Artifact ID to update",
                required=True,
            ),
            ToolParameter(
                name="old_str",
                type="string",
                description="Text to be replaced",
                required=True,
            ),
            ToolParameter(
                name="new_str",
                type="string",
                description="New text to replace with",
                required=True,
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        success, message, match_info = await self._service.update_artifact(
            session_id=session_id,
            artifact_id=params["id"],
            old_str=params["old_str"],
            new_str=params["new_str"],
        )

        # Surface fuzzy_stats even on failure so observability gets the bail
        # reason (covers all of bail_low_entropy / no_anchor / budget /
        # deadline / ambiguous / no_window).
        if not success:
            metadata: Dict[str, Any] = {}
            if match_info and "fuzzy_stats" in match_info:
                metadata["fuzzy_stats"] = match_info["fuzzy_stats"]
            return ToolResult(success=False, error=message, metadata=metadata)

        logger.info(message)

        memory = await self._service.get_artifact(session_id, params["id"])
        version = memory.current_version if memory else None
        match_type = (match_info or {}).get("match_type")

        if match_type in ("fuzzy", "normalized") and match_info:
            # Both layers carry an expected/matched diff worth surfacing
            # symmetrically. Use distinct attribute / block names so the
            # downstream model can tell which layer fired.
            similarity = f"{match_info['similarity']:.1%}"
            expected = _truncate_middle(match_info["expected_text"], 200)
            matched = _truncate_middle(match_info["matched_text"], 200)
            attr_name = "fuzzy" if match_type == "fuzzy" else "normalized"
            block_name = "fuzzy_detail" if match_type == "fuzzy" else "normalize_detail"
            xml = (
                f'<artifact version="{version}" {attr_name}="{similarity}">'
                f"\n  <id>{params['id']}</id>"
                f"\n  {message}"
                f"\n  <{block_name}>"
                f"\n    <expected>{expected}</expected>"
                f"\n    <matched>{matched}</matched>"
                f"\n  </{block_name}>"
                f"\n</artifact>"
            )
        else:
            xml = f'<artifact version="{version}"><id>{params["id"]}</id> {message}</artifact>'

        return ToolResult(success=True, data=xml, metadata=(match_info or {}))

    def to_xml_example(self) -> str:
        return """<tool_call>
  <reason><![CDATA[mark research topic X as completed in the task plan]]></reason>
  <name>update_artifact</name>
  <params>
    <id><![CDATA[task_plan]]></id>
    <old_str><![CDATA[1. [✗] Research topic X
   - Status: pending
   - Assigned: research_agent
   - Notes: N/A]]></old_str>
    <new_str><![CDATA[1. [✓] Research topic X
   - Status: completed
   - Assigned: research_agent
   - Notes: See artifact research_topic_x]]></new_str>
  </params>
</tool_call>"""
