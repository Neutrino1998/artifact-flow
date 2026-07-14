"""Pure bounded text matching shared by backend tools and the sandbox.

The public dispatcher follows three progressively looser layers:

* exact: one literal occurrence
* normalized: Unicode/typographic normalization with spans mapped to source text
* auto: exact, normalized, then anchor-bounded Levenshtein matching

Every successful mode identifies exactly one source span. Ambiguity and exhausted
budgets fail loudly; callers never receive a guessed winner.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Union

from rapidfuzz.distance import Levenshtein


MatchMode = Literal["exact", "normalized", "auto"]
Span = Tuple[int, int]
DistanceFn = Callable[..., int]


@dataclass(frozen=True)
class MatchLimits:
    """Resource and quality bounds for fuzzy matching."""

    anchor_shingle_len: int = 6
    anchor_min_usable_len: int = 3
    anchor_max_occurrences: int = 20
    max_unique_centers: int = 50
    max_fuzzy_wall_clock_ms: int = 500
    fuzzy_max_l_dist: int = 16
    fuzzy_max_ratio: float = 0.10
    max_fuzzy_old_str_len: int = 10_000


DEFAULT_LIMITS = MatchLimits()


_CJK_RE = (
    r'[⺀-⿟'
    r'　-〿'
    r'぀-ゟ'
    r'゠-ヿ'
    r'㐀-䶿'
    r'一-鿿'
    r'豈-﫿'
    r'︰-﹏'
    r'＀-￯'
    r'\U00020000-\U0002a6df'
    r']'
)

_SMART_QUOTES = str.maketrans({
    '‘': "'", '’': "'", '‚': "'", '“': '"', '”': '"', '„': '"',
    '‹': "'", '›': "'", '«': '"', '»': '"',
})
_UNICODE_DASHES = str.maketrans({
    '‒': '-', '–': '-', '—': '-', '―': '-', '−': '-', '﹘': '-',
    '﹣': '-', '－': '-',
})
_SPECIAL_SPACES = str.maketrans({
    ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',
    ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',
    ' ': ' ', ' ': ' ', '　': ' ',
})
_ALL_CHAR_TRANSLATES = {**_SMART_QUOTES, **_UNICODE_DASHES, **_SPECIAL_SPACES}


def _nfkc_span_map(pre: str, post: str) -> list[Span]:
    """Build a map from each NFKC output character to its source span."""
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


def normalize_for_match(text: str) -> tuple[str, list[Span]]:
    """Normalize text while retaining source spans for every output character."""
    translated = text.translate(_ALL_CHAR_TRANSLATES)
    nfkc_text = unicodedata.normalize('NFKC', translated)
    spans = _nfkc_span_map(translated, nfkc_text)

    stripped_chars: list[str] = []
    stripped_spans: list[Span] = []
    line_chars: list[str] = []
    line_spans: list[Span] = []

    for char, span in zip(nfkc_text, spans):
        if char == '\n':
            while line_chars and line_chars[-1] == ' ':
                line_chars.pop()
                line_spans.pop()
            stripped_chars.extend(line_chars)
            stripped_spans.extend(line_spans)
            stripped_chars.append(char)
            stripped_spans.append(span)
            line_chars.clear()
            line_spans.clear()
        else:
            line_chars.append(char)
            line_spans.append(span)

    while line_chars and line_chars[-1] == ' ':
        line_chars.pop()
        line_spans.pop()
    stripped_chars.extend(line_chars)
    stripped_spans.extend(line_spans)

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
                if ((prev_is_cjk and next_is_latin)
                        or (prev_is_latin and next_is_cjk)):
                    i = j
                    continue

        result.append(stripped_chars[i])
        result_spans.append(stripped_spans[i])
        i += 1

    return ''.join(result), result_spans


@dataclass
class FuzzyMatch:
    start: int
    end: int
    distance: int
    similarity: float
    matched_text: str
    fuzzy_stats: Dict[str, Any]


@dataclass
class FuzzyBail:
    outcome: str
    message: str
    fuzzy_stats: Optional[Dict[str, Any]] = None


FuzzyResult = Union[FuzzyMatch, FuzzyBail]


@dataclass
class MatchInfo:
    success: bool
    message: str
    new_content: Optional[str] = None
    match_type: Optional[str] = None
    similarity: Optional[float] = None
    expected_text: Optional[str] = None
    matched_text: Optional[str] = None
    changes: Optional[List[Tuple[str, str]]] = None
    fuzzy_stats: Optional[Dict[str, Any]] = None
    offset: Optional[int] = None
    deleted_len: Optional[int] = None


@dataclass
class SegmentMatchInfo:
    success: bool
    message: str
    segment_index: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    match_type: Optional[str] = None
    similarity: Optional[float] = None
    matched_text: Optional[str] = None
    fuzzy_stats: Optional[Dict[str, Any]] = None


_LOW_INFO_CHARS = frozenset(" \t|-_=.,;:0123456789")


def _is_low_info_shingle(value: str) -> bool:
    return all(char in _LOW_INFO_CHARS for char in value)


def _hash_old_str(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    stats: Dict[str, Any] = {
        "m": m,
        "n": n,
        "k": k,
        "L": L,
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


def find_fuzzy_match(
    old_str: str,
    content: str,
    *,
    limits: MatchLimits = DEFAULT_LIMITS,
    distance_fn: Optional[DistanceFn] = None,
    deadline: Optional[float] = None,
) -> FuzzyResult:
    """Locate one anchor-bounded fuzzy span within explicit resource limits."""
    distance_fn = distance_fn or Levenshtein.distance
    m = len(old_str)
    n = len(content)
    old_hash = _hash_old_str(old_str)

    if m > limits.max_fuzzy_old_str_len:
        return FuzzyBail(
            outcome="bail_budget",
            message=(
                f"old_str 长度 {m} 超出 Layer 2 上界 "
                f"{limits.max_fuzzy_old_str_len}:请缩小 old_str 范围"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=0, L=0, old_hash=old_hash, outcome="bail_budget"
            ),
        )

    allowed_dist = min(
        limits.fuzzy_max_l_dist,
        max(1, int(m * limits.fuzzy_max_ratio)),
    )
    L = min(limits.anchor_shingle_len, m // (allowed_dist + 1))
    if L < limits.anchor_min_usable_len:
        return FuzzyBail(
            outcome="bail_low_entropy",
            message="old_str 太短或与目标差异过大,无法可靠定位:请提供更长 / 更独特的上下文",
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                outcome="bail_low_entropy",
            ),
        )

    started_at = monotonic()
    local_deadline = started_at + limits.max_fuzzy_wall_clock_ms / 1000
    effective_deadline = min(local_deadline, deadline) if deadline is not None else local_deadline

    old_pos: Dict[str, List[int]] = defaultdict(list)
    for p in range(m - L + 1):
        shingle = old_str[p:p + L]
        if _is_low_info_shingle(shingle):
            continue
        old_pos[shingle].append(p)

    if not old_pos:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_anchor",
            message=(
                "old_str 全部由低信息字符(空白 / 数字 / 标点)构成,无法定位:"
                "请提供包含具体词汇的上下文"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                elapsed_ms=elapsed_ms, outcome="bail_no_anchor",
            ),
        )

    positions: Dict[str, List[int]] = defaultdict(list)
    common: set[str] = set()
    for i in range(n - L + 1):
        shingle = content[i:i + L]
        if shingle in old_pos and shingle not in common:
            positions[shingle].append(i)
            if len(positions[shingle]) > limits.anchor_max_occurrences:
                common.add(shingle)
                del positions[shingle]

    rare_count = len(positions)
    if rare_count == 0:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_anchor",
            message=(
                "old_str 太重复或文档已大幅漂移:"
                "请重新读取后提供更独特的上下文"
            ),
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                elapsed_ms=elapsed_ms, outcome="bail_no_anchor",
            ),
        )

    raw_centers: List[int] = []
    for shingle, q_list in positions.items():
        for p in old_pos[shingle]:
            for q in q_list:
                raw_centers.append(q - p)

    raw_centers.sort()
    unique_centers: List[int] = []
    for center in raw_centers:
        if not unique_centers or center - unique_centers[-1] > allowed_dist:
            unique_centers.append(center)

    if len(unique_centers) > limits.max_unique_centers:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_budget",
            message="old_str 在文档中触发过多候选对齐:请提供更独特的上下文",
            fuzzy_stats=_build_stats(
                m=m, n=n, k=allowed_dist, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers), elapsed_ms=elapsed_ms,
                outcome="bail_budget",
            ),
        )

    k = allowed_dist
    verify_calls = 0
    matches: List[Tuple[int, int, int]] = []
    bailed_deadline = monotonic() > effective_deadline
    span_tied = False

    for center_start in unique_centers:
        if bailed_deadline:
            break
        center_end = center_start + m
        best: Optional[Tuple[int, int, int]] = None
        tied_at_best = False
        for ds in range(-k, k + 1):
            if bailed_deadline:
                break
            for de in range(-k, k + 1):
                if monotonic() > effective_deadline:
                    bailed_deadline = True
                    break
                match_start = max(0, center_start + ds)
                match_end = min(n, center_end + de)
                if match_end - match_start <= 0:
                    continue
                if abs((match_end - match_start) - m) > k:
                    continue
                distance = distance_fn(
                    old_str,
                    content[match_start:match_end],
                    score_cutoff=k,
                )
                verify_calls += 1
                if distance > k:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, match_start, match_end)
                    tied_at_best = False
                elif distance == best[0] and (match_start, match_end) != (best[1], best[2]):
                    tied_at_best = True
        if best is not None:
            matches.append(best)
            if tied_at_best:
                span_tied = True

    if bailed_deadline:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_deadline",
            message="old_str 在文档中触发过多候选对齐:请提供更独特的上下文",
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers), verify_calls=verify_calls,
                elapsed_ms=elapsed_ms, outcome="bail_deadline",
            ),
        )

    if not matches:
        elapsed_ms = int((monotonic() - started_at) * 1000)
        return FuzzyBail(
            outcome="bail_no_window",
            message="未找到满足相似度的窗口:请重新读取后提供更独特的上下文",
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers), verify_calls=verify_calls,
                elapsed_ms=elapsed_ms, outcome="bail_no_window",
            ),
        )

    matches.sort(key=lambda item: (item[1], item[2]))
    regions: List[Tuple[int, int, int]] = []
    for distance, match_start, match_end in matches:
        merged = False
        for idx, (region_distance, region_start, region_end) in enumerate(regions):
            if abs(match_start - region_start) <= k and abs(match_end - region_end) <= k:
                if distance < region_distance:
                    regions[idx] = (distance, match_start, match_end)
                elif distance == region_distance and (match_start, match_end) != (region_start, region_end):
                    span_tied = True
                merged = True
                break
        if not merged:
            regions.append((distance, match_start, match_end))

    elapsed_ms = int((monotonic() - started_at) * 1000)
    if span_tied or len(regions) >= 2:
        return FuzzyBail(
            outcome="bail_ambiguous",
            message="old_str 在文档中有多个候选位置:请扩展上下文使其唯一",
            fuzzy_stats=_build_stats(
                m=m, n=n, k=k, L=L, old_hash=old_hash,
                rare_shingles=rare_count, raw_centers=len(raw_centers),
                unique_centers=len(unique_centers), verify_calls=verify_calls,
                elapsed_ms=elapsed_ms, outcome="bail_ambiguous",
            ),
        )

    best_distance, best_start, best_end = regions[0]
    matched_text = content[best_start:best_end]
    similarity = 1.0 - best_distance / max(m, best_end - best_start)
    return FuzzyMatch(
        start=best_start,
        end=best_end,
        distance=best_distance,
        similarity=similarity,
        matched_text=matched_text,
        fuzzy_stats=_build_stats(
            m=m, n=n, k=k, L=L, old_hash=old_hash,
            rare_shingles=rare_count, raw_centers=len(raw_centers),
            unique_centers=len(unique_centers), verify_calls=verify_calls,
            elapsed_ms=elapsed_ms, outcome="matched", distance=best_distance,
            similarity_pct=round(similarity * 100, 1),
        ),
    )


def _normalized_occurrences(content: str, old_str: str) -> tuple[int, list[Span]]:
    norm_old, _ = normalize_for_match(old_str)
    norm_content, source_spans = normalize_for_match(content)
    if not norm_old:
        return 0, []

    raw_count = 0
    valid_spans: list[Span] = []
    offset = 0
    while True:
        norm_start = norm_content.find(norm_old, offset)
        if norm_start < 0:
            break
        raw_count += 1
        norm_end = norm_start + len(norm_old)
        starts_mid = (
            norm_start > 0
            and source_spans[norm_start] == source_spans[norm_start - 1]
        )
        ends_mid = (
            norm_end < len(source_spans)
            and source_spans[norm_end] == source_spans[norm_end - 1]
        )
        if not (starts_mid or ends_mid):
            valid_spans.append((
                source_spans[norm_start][0],
                source_spans[norm_end - 1][1],
            ))
        offset = norm_end
    return raw_count, valid_spans


def compute_update(
    content: str,
    old_str: str,
    new_str: str,
    *,
    mode: MatchMode = "auto",
    limits: MatchLimits = DEFAULT_LIMITS,
    distance_fn: Optional[DistanceFn] = None,
) -> MatchInfo:
    """Replace one unique span and return its original-content coordinates."""
    if mode not in {"exact", "normalized", "auto"}:
        raise ValueError(f"unsupported match mode: {mode}")
    if not old_str:
        return MatchInfo(success=False, message="old_str must not be empty")

    if old_str in content:
        count = content.count(old_str)
        if count > 1:
            return MatchInfo(
                success=False,
                message=f"Text '{old_str[:50]}...' appears {count} times (must be unique)",
            )
        offset = content.index(old_str)
        return MatchInfo(
            success=True,
            message="exact match",
            new_content=content[:offset] + new_str + content[offset + len(old_str):],
            match_type="exact",
            similarity=1.0,
            changes=[(old_str, new_str)],
            offset=offset,
            deleted_len=len(old_str),
        )

    if mode == "exact":
        return MatchInfo(success=False, message="exact text not found")

    # A miss on an oversized needle must not enter normalization: span-map
    # construction is O(m) Python work and precedes the fuzzy deadline.
    if len(old_str) > limits.max_fuzzy_old_str_len:
        oversized = find_fuzzy_match(
            old_str,
            content,
            limits=limits,
            distance_fn=distance_fn,
        )
        return MatchInfo(
            success=False,
            message=oversized.message,
            fuzzy_stats=oversized.fuzzy_stats,
        )

    raw_count, normalized_spans = _normalized_occurrences(content, old_str)
    if raw_count > 1:
        return MatchInfo(
            success=False,
            message=(
                f"Text '{old_str[:50]}...' appears {raw_count} times after "
                "normalization (must be unique)"
            ),
        )
    if raw_count == 1 and normalized_spans:
        orig_start, orig_end = normalized_spans[0]
        matched_text = content[orig_start:orig_end]
        similarity = 1.0 - (
            abs(len(matched_text) - len(old_str))
            / max(len(matched_text), len(old_str))
        )
        return MatchInfo(
            success=True,
            message=f"normalized match {similarity:.1%}",
            new_content=content[:orig_start] + new_str + content[orig_end:],
            match_type="normalized",
            similarity=similarity,
            expected_text=old_str,
            matched_text=matched_text,
            changes=[(matched_text, new_str)],
            offset=orig_start,
            deleted_len=orig_end - orig_start,
        )

    if mode == "normalized":
        return MatchInfo(success=False, message="normalized text not found")

    fuzzy_result = find_fuzzy_match(
        old_str,
        content,
        limits=limits,
        distance_fn=distance_fn,
    )
    if isinstance(fuzzy_result, FuzzyBail):
        return MatchInfo(
            success=False,
            message=fuzzy_result.message,
            fuzzy_stats=fuzzy_result.fuzzy_stats,
        )

    return MatchInfo(
        success=True,
        message=f"fuzzy match {fuzzy_result.similarity:.1%}",
        new_content=(
            content[:fuzzy_result.start]
            + new_str
            + content[fuzzy_result.end:]
        ),
        match_type="fuzzy",
        similarity=fuzzy_result.similarity,
        expected_text=old_str,
        matched_text=fuzzy_result.matched_text,
        changes=[(fuzzy_result.matched_text, new_str)],
        fuzzy_stats=fuzzy_result.fuzzy_stats,
        offset=fuzzy_result.start,
        deleted_len=fuzzy_result.end - fuzzy_result.start,
    )


def find_unique_in_segments(
    segments: Sequence[str],
    old_str: str,
    *,
    mode: MatchMode = "auto",
    limits: MatchLimits = DEFAULT_LIMITS,
    distance_fn: Optional[DistanceFn] = None,
) -> SegmentMatchInfo:
    """Find one match across independent containers such as paragraphs."""
    if mode not in {"exact", "normalized", "auto"}:
        raise ValueError(f"unsupported match mode: {mode}")
    if not old_str:
        return SegmentMatchInfo(success=False, message="old_str must not be empty")

    exact_matches: list[tuple[int, int, int]] = []
    for segment_index, segment in enumerate(segments):
        offset = 0
        while True:
            start = segment.find(old_str, offset)
            if start < 0:
                break
            exact_matches.append((segment_index, start, start + len(old_str)))
            if len(exact_matches) > 1:
                return SegmentMatchInfo(
                    success=False,
                    message="text appears multiple times across editable segments",
                )
            offset = start + len(old_str)
    if exact_matches:
        segment_index, start, end = exact_matches[0]
        return SegmentMatchInfo(
            success=True,
            message="exact match",
            segment_index=segment_index,
            start=start,
            end=end,
            match_type="exact",
            similarity=1.0,
            matched_text=segments[segment_index][start:end],
        )
    if mode == "exact":
        return SegmentMatchInfo(success=False, message="exact text not found")

    if len(old_str) > limits.max_fuzzy_old_str_len:
        oversized = find_fuzzy_match(
            old_str,
            "",
            limits=limits,
            distance_fn=distance_fn,
        )
        return SegmentMatchInfo(
            success=False,
            message=oversized.message,
            fuzzy_stats=oversized.fuzzy_stats,
        )

    normalized_raw_count = 0
    normalized_matches: list[tuple[int, int, int]] = []
    for segment_index, segment in enumerate(segments):
        raw_count, spans = _normalized_occurrences(segment, old_str)
        normalized_raw_count += raw_count
        normalized_matches.extend(
            (segment_index, start, end) for start, end in spans
        )
        if normalized_raw_count > 1:
            return SegmentMatchInfo(
                success=False,
                message="text appears multiple times after normalization",
            )
    if normalized_raw_count == 1 and normalized_matches:
        segment_index, start, end = normalized_matches[0]
        matched_text = segments[segment_index][start:end]
        similarity = 1.0 - (
            abs(len(matched_text) - len(old_str))
            / max(len(matched_text), len(old_str))
        )
        return SegmentMatchInfo(
            success=True,
            message=f"normalized match {similarity:.1%}",
            segment_index=segment_index,
            start=start,
            end=end,
            match_type="normalized",
            similarity=similarity,
            matched_text=matched_text,
        )
    if mode == "normalized":
        return SegmentMatchInfo(success=False, message="normalized text not found")

    deadline = monotonic() + limits.max_fuzzy_wall_clock_ms / 1000
    fuzzy_matches: list[tuple[int, FuzzyMatch]] = []
    last_stats: Optional[Dict[str, Any]] = None
    for segment_index, segment in enumerate(segments):
        if monotonic() > deadline:
            return SegmentMatchInfo(
                success=False,
                message="fuzzy matching deadline exceeded",
                fuzzy_stats=last_stats,
            )
        result = find_fuzzy_match(
            old_str,
            segment,
            limits=limits,
            distance_fn=distance_fn,
            deadline=deadline,
        )
        last_stats = result.fuzzy_stats
        if isinstance(result, FuzzyMatch):
            fuzzy_matches.append((segment_index, result))
            if len(fuzzy_matches) > 1:
                return SegmentMatchInfo(
                    success=False,
                    message="text has multiple fuzzy candidate segments",
                    fuzzy_stats=result.fuzzy_stats,
                )
        elif result.outcome in {
            "bail_ambiguous",
            "bail_budget",
            "bail_deadline",
            "bail_low_entropy",
        }:
            return SegmentMatchInfo(
                success=False,
                message=result.message,
                fuzzy_stats=result.fuzzy_stats,
            )

    if not fuzzy_matches:
        return SegmentMatchInfo(
            success=False,
            message="no unique fuzzy match found",
            fuzzy_stats=last_stats,
        )

    segment_index, result = fuzzy_matches[0]
    return SegmentMatchInfo(
        success=True,
        message=f"fuzzy match {result.similarity:.1%}",
        segment_index=segment_index,
        start=result.start,
        end=result.end,
        match_type="fuzzy",
        similarity=result.similarity,
        matched_text=result.matched_text,
        fuzzy_stats=result.fuzzy_stats,
    )
