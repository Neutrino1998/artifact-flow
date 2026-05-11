"""
Grep across artifact content with ripgrep-faithful semantics.

设计要点（详见 plan/CLAUDE.md "Minimize tool parameter surface"）:
- 单 / session 范围由 `id` 是否传入区分（对应 `rg pattern path` vs `rg pattern`）
- pattern 默认 Python `re` regex；`fixed_strings=true` 走 `re.escape` 切 literal
- 参数表面只暴露模型有语义意图的项；`SESSION_GREP_MAX_TOTAL` 隐藏在 config
- 输出对齐 ripgrep TTY 行为：单 flat、session heading；命中行 `:`、context 行 `-`、
  group 间 `--`
"""

import bisect
import re
from typing import TYPE_CHECKING, List, Optional, Tuple

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult

if TYPE_CHECKING:
    from tools.builtin.artifact_ops import ArtifactManager


# ============================================================
# 模块级纯函数（便于单测）
# ============================================================


def _compile_pattern(pattern: str, fixed_strings: bool, ignore_case: bool) -> "re.Pattern[str]":
    """编译 pattern。`fixed_strings=True` 先 `re.escape` 再编译，把 regex 元字符
    全部当字面看。`re.MULTILINE` 让 `^`/`$` 按行匹配（虽然我们是逐行 scan，但
    模型可能仍写 `^foo`/`bar$` 这样的 anchor）。"""
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    if fixed_strings:
        pattern = re.escape(pattern)
    return re.compile(pattern, flags)


def _scan_content(
    content: str,
    regex: "re.Pattern[str]",
    context: int,
    max_count: int,
) -> List[Tuple[int, str, bool]]:
    """对整 artifact 跑 `regex.finditer(content)`，再把 match.start() 映射回行号。

    - 全文匹配（不是逐行）兑现 description 的 "Python `re` syntax" 契约:
      `\\A`/`\\Z` 真正指 artifact 边界、`foo\\nbar` 这类跨行 pattern 也能命中
    - 跨行 match 只在**起始行**打点（ripgrep `-U` 多行模式行为，避免一次匹配同时
      标记多行带来命中计数歧义）
    - 同一行多次命中去重（按行算 1 个命中）
    - **零宽匹配整体 drop** —— `\\A` / `^` / `\\b` / `\\Z` / `^$` 等无可见内容的
      命中不进结果。这避免了"从源码字符串启发式判 anchor 意图"的不可靠路径
      (Python `re` 没暴露 AST,任何 string-level 判断都有 false positive)。
      若需"找被空行分隔的段落",改用内容侧锚点(例:markdown 的 `^# `、小说的
      `^第.*章`、Python 的 `^class `),都是非零宽 pattern,正常工作。
    - max_count 限制 **行级命中** 数；context 行不计入

    返回 [(line_no_1indexed, line_text, is_match), ...]:
    - is_match=True 是命中行；False 是 context 行
    - 相邻命中的 context 重叠 → sliding skip 合并去重
    """
    if not content or max_count <= 0:
        return []

    lines = content.splitlines()  # 用于显示，去掉行尾分隔符
    if not lines:
        return []

    # 行起点偏移表:line_starts[i] = 第 i 行在 content 里的 0-indexed 起始位置
    # 用 splitlines(keepends=True) 保留分隔符长度以正确累加偏移（\n/\r\n/\r 兼容）
    kept = content.splitlines(keepends=True)
    line_starts: List[int] = []
    pos = 0
    for kl in kept:
        line_starts.append(pos)
        pos += len(kl)

    # 全文跑 finditer，把每个 match.start() 通过 bisect 映射到行号
    match_line_indices: List[int] = []  # 按命中顺序、已去重
    seen_lines = set()
    for m in regex.finditer(content):
        # 零宽匹配整体 drop。从源码启发式判 anchor 意图(`^$` 找空行 vs `\A`
        # 只是起始锚)做不到无 false positive —— Python `re` 没暴露 AST,
        # 任何 string-level 判断都有边界。换 "全 drop" 换简单 + 可证明:
        # 跨 reviewer 任何反例,零宽就是零宽,不报。失去的能力(`^$` 找空行)
        # agent 在 ArtifactFlow 里没有真实用法,可用内容侧锚点替代。
        if m.start() == m.end():
            continue
        idx = bisect.bisect_right(line_starts, m.start()) - 1
        if idx < 0 or idx >= len(lines):
            continue
        if idx in seen_lines:
            continue
        seen_lines.add(idx)
        match_line_indices.append(idx)
        if len(match_line_indices) >= max_count:
            break

    if not match_line_indices:
        return []

    # 构造输出窗口:按命中顺序展开 ±context，相邻 window 重叠时 sliding skip
    hits: List[Tuple[int, str, bool]] = []
    last_emitted = -1  # 已加入 hits 的最高 0-indexed 行号
    last_idx = len(lines) - 1

    for m in match_line_indices:
        start = max(0, m - context)
        end = min(last_idx, m + context)
        actual_start = max(start, last_emitted + 1)
        for i in range(actual_start, end + 1):
            is_match = i in seen_lines
            hits.append((i + 1, lines[i], is_match))
        if end > last_emitted:
            last_emitted = end

    return hits


def _format_flat(hits: List[Tuple[int, str, bool]]) -> str:
    """单 artifact 输出格式。命中行 `N:content`，context 行 `N-content`，
    相邻行号间断 → 插入 `--`（ripgrep 行为）。

    空 hits 返回空串（调用方应在调用前判 No-matches，不应进到这里）。
    """
    out: List[str] = []
    prev_lineno: Optional[int] = None
    for lineno, text, is_match in hits:
        if prev_lineno is not None and lineno > prev_lineno + 1:
            out.append("--")
        sep = ":" if is_match else "-"
        out.append(f"{lineno}{sep}{text}")
        prev_lineno = lineno
    return "\n".join(out)


def _format_heading(grouped: List[Tuple[str, List[Tuple[int, str, bool]]]]) -> str:
    """session 模式：每个 artifact 一段 heading + flat 块，块间空行（ripgrep TTY 行为）。"""
    blocks: List[str] = []
    for aid, hits in grouped:
        blocks.append(f"{aid}\n{_format_flat(hits)}")
    return "\n\n".join(blocks)


# ============================================================
# Tool
# ============================================================


class GrepArtifactTool(BaseTool):
    """跨 artifact 内容检索，ripgrep 语义。"""

    def __init__(self, manager: "Optional[ArtifactManager]" = None):
        super().__init__(
            name="grep_artifact",
            description=(
                "Search artifact content. Pass id=... to scope to a single artifact "
                "(flat 'lineno:content' output); omit to search all artifacts in the current "
                "session (heading-style output, one block per artifact). "
                "Pattern is a Python `re` regex by default; set fixed_strings=true to match literally. "
                "Returns 'No matches for <pattern>' on empty hit; invalid regex returns success=false."
            ),
            permission=ToolPermission.AUTO,
            # 沿用 BaseTool 默认 max_result_size_chars=50000：grep 结果若超阈值，
            # 引擎中间件会把它落盘成新 artifact，模型下次用 read_artifact 分段取。
        )
        self._manager = manager

    def set_manager(self, manager: "ArtifactManager") -> None:
        """依赖注入入口（跟其他 artifact 工具一致）。"""
        self._manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="pattern",
                type="string",
                description=(
                    "Search pattern. Python `re` regex by default — use fixed_strings=true "
                    "to disable regex semantics and match literally."
                ),
                required=True,
            ),
            ToolParameter(
                name="id",
                type="string",
                description=(
                    "Artifact ID to search. Omit to grep every artifact in the current session "
                    "(heading-style output)."
                ),
                required=False,
                default=None,
            ),
            ToolParameter(
                name="fixed_strings",
                type="boolean",
                description="Treat pattern as a literal string (ripgrep -F).",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="ignore_case",
                type="boolean",
                description="Case-insensitive match (ripgrep -i).",
                required=False,
                default=False,
            ),
            ToolParameter(
                name="context",
                type="integer",
                description="Symmetric context lines around each match (ripgrep -C).",
                required=False,
                default=0,
            ),
            ToolParameter(
                name="max_count",
                type="integer",
                description="Maximum matches per artifact (ripgrep -m). Default 20.",
                required=False,
                default=20,
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        if not self._manager:
            return ToolResult(success=False, error="ArtifactManager not configured")

        session_id = self._manager.current_session_id
        if not session_id:
            return ToolResult(success=False, error="No active session")

        pattern: str = params["pattern"]
        aid: Optional[str] = params.get("id")
        fixed_strings: bool = bool(params.get("fixed_strings", False))
        ignore_case: bool = bool(params.get("ignore_case", False))
        # 不能用 `... or 0` / `... or 20` —— 用户显式传 0 会被 falsy 误判替换成默认值
        context_lines: int = int(params.get("context", 0))
        max_count: int = int(params.get("max_count", 20))

        # context 负数无意义；clamp 到 0
        if context_lines < 0:
            context_lines = 0
        # max_count <= 0 等价于"不要任何结果"；视作 0 命中，给个明确信号
        if max_count <= 0:
            return ToolResult(success=True, data=f"No matches for {pattern!r}")

        try:
            regex = _compile_pattern(pattern, fixed_strings, ignore_case)
        except re.error as e:
            return ToolResult(success=False, error=f"Invalid regex: {e}")

        # ─── 单 artifact 模式 ──────────────────────────────────────
        if aid is not None:
            result = await self._manager.read_artifact(
                session_id=session_id, artifact_id=aid, version=None
            )
            if result is None:
                return ToolResult(success=False, error=f"Artifact '{aid}' not found")

            content = result.get("content", "") or ""
            hits = _scan_content(content, regex, context_lines, max_count)
            match_count = sum(1 for _, _, is_match in hits if is_match)

            if match_count == 0:
                return ToolResult(success=True, data=f"No matches for {pattern!r}")

            body = _format_flat(hits)
            data = f"{body}\n\n{match_count} matches"
            return ToolResult(success=True, data=data)

        # ─── Session 模式 ──────────────────────────────────────────
        artifacts = await self._manager.list_artifacts(
            session_id=session_id, include_content=True
        )

        grouped: List[Tuple[str, List[Tuple[int, str, bool]]]] = []
        total_hits = 0
        session_cap = config.SESSION_GREP_MAX_TOTAL

        for art in artifacts:
            content = art.get("content", "") or ""
            if not content:
                continue

            remaining = session_cap - total_hits
            if remaining <= 0:
                break

            effective_cap = min(max_count, remaining)
            hits = _scan_content(content, regex, context_lines, effective_cap)
            match_count = sum(1 for _, _, is_match in hits if is_match)
            if match_count == 0:
                continue

            grouped.append((art["id"], hits))
            total_hits += match_count

            if total_hits >= session_cap:
                break

        if not grouped:
            return ToolResult(success=True, data=f"No matches for {pattern!r}")

        body = _format_heading(grouped)
        summary = f"{total_hits} matches across {len(grouped)} artifacts"
        if total_hits >= session_cap:
            summary += (
                f". Hit session cap ({session_cap}). "
                f"Refine pattern or pass id=... to narrow."
            )

        data = f"{body}\n\n{summary}"
        return ToolResult(success=True, data=data)
