"""
Grep across artifact content with ripgrep-faithful semantics.

设计要点（详见 plan/CLAUDE.md "Minimize tool parameter surface"）:
- 单 / session 范围由 `id` 是否传入区分（对应 `rg pattern path` vs `rg pattern`）
- pattern 默认 **RE2 regex**（ripgrep / Rust-regex 同族：线性时间、无回溯 → 结构性免疫
  ReDoS）；`fixed_strings=true` 走 `re2.escape` 切 literal。换 RE2 是这个工具的本意
  —— 它自称 "ripgrep 语义"，而 ripgrep 底层正是不回溯、无 backref 的自动机引擎；旧的
  Python `re` 才是会被 `(a+)+$` 卡死事件循环的那个（2026-05-14 事故同源失败模式）。
- 资源护栏：RE2 线性 → 输入封顶（`GREP_CONTENT_MAX_CHARS` / `GREP_SESSION_SCAN_BUDGET_CHARS`）
  即算法界，**无需墙钟 timeout**（与 update_artifact 的回溯型 fuzzy 不同——那里墙钟是
  唯一能兜底的手段，这里结构性线性，封顶即够）。session 模式惰性逐 artifact 载入，峰值
  内存收敛到单份 content。
- 参数表面只暴露模型有语义意图的项；护栏常量全隐藏在 config
- 输出对齐 ripgrep TTY 行为：单 flat、session heading；命中行 `:`、context 行 `-`、
  group 间 `--`
"""

import bisect
from typing import TYPE_CHECKING, List, Optional, Tuple

import re2

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult

if TYPE_CHECKING:
    from tools.builtin.artifact_ops import ArtifactManager


# ============================================================
# 模块级纯函数（便于单测）
# ============================================================


def _compile_pattern(pattern: str, fixed_strings: bool, ignore_case: bool) -> "re2._Regexp":
    """编译 pattern（RE2 引擎，线性时间、无回溯 → 抗 ReDoS）。

    - `fixed_strings=True` 先 `re2.escape` 把 regex 元字符全部当字面看。
    - flags 用内联前缀（RE2 不暴露 `re.MULTILINE` 那样的模块常量）：始终 `(?m)`
      让 `^`/`$` 按行匹配（对齐旧 `re.MULTILINE` 行为，模型可能写 `^foo`/`bar$`），
      `ignore_case` 时再加 `(?i)`。
    - `Options(log_errors=False)` 压住 RE2 编译失败时打到 STDERR 的 absl 日志
      （否则每个非法 pattern 都会污染 docker logs / 主应用日志流）。

    注意方言差异（已在 description 告知模型）：RE2 不支持 backreference / look-around
    （编译期 `re2.error` 响亮失败），end-of-input 用 `\\z` 而非 Python 的 `\\Z`。
    """
    if fixed_strings:
        pattern = re2.escape(pattern)
    prefix = "(?m)" + ("(?i)" if ignore_case else "")
    options = re2.Options()
    options.log_errors = False
    return re2.compile(prefix + pattern, options)


def _scan_content(
    content: str,
    regex: "re2._Regexp",
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
        # 只是起始锚)做不到无 false positive —— 引擎没暴露 AST,任何 string-level
        # 判断都有边界。换 "全 drop" 换简单 + 可证明:跨 reviewer 任何反例,零宽
        # 就是零宽,不报。失去的能力(`^$` 找空行)agent 在 ArtifactFlow 里没有真实
        # 用法,可用内容侧锚点替代。
        # 附带好处:RE2 的 finditer 会在行边界**重复**返回零宽 anchor 命中
        # (实测 `(?m)^` 对 "foo\nbar\n" 给 [(0,0),(4,4),(4,4),(8,8),(8,8)]),
        # 这条 drop 把差异一并中和 —— 无需为 RE2 单独处理。
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
                "Pattern is an RE2 regex by default (ripgrep-style: linear-time, no "
                "backreferences or look-around; use \\z, not \\Z, for end-of-input); set "
                "fixed_strings=true to match literally. "
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
                    "Search pattern. RE2 regex by default (ripgrep-style; no backreferences "
                    "or look-around; \\z for end-of-input) — use fixed_strings=true to disable "
                    "regex semantics and match literally."
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

        # pattern 长度上界 —— 挡病态超长 pattern（RE2 编译侧另有 max_mem=8MiB 兜底）
        if len(pattern) > config.GREP_MAX_PATTERN_CHARS:
            return ToolResult(
                success=False,
                error=f"Pattern too long (>{config.GREP_MAX_PATTERN_CHARS} chars). Simplify it.",
            )

        # context 负数无意义 clamp 到 0；上界 clamp 防超大窗口铺满全文（GREP-03）
        context_lines = max(0, min(context_lines, config.GREP_MAX_CONTEXT))
        # max_count <= 0 等价于"不要任何结果"；视作 0 命中，给个明确信号
        if max_count <= 0:
            return ToolResult(success=True, data=f"No matches for {pattern!r}")
        max_count = min(max_count, config.GREP_MAX_COUNT)

        try:
            regex = _compile_pattern(pattern, fixed_strings, ignore_case)
        except re2.error as e:
            # re2.error 的 args[0] 是 bytes（如 b'missing ): ...'）；解码成模型可读文案
            detail = e.args[0] if getattr(e, "args", None) else e
            if isinstance(detail, (bytes, bytearray)):
                detail = detail.decode("utf-8", "replace")
            return ToolResult(
                success=False,
                error=(
                    f"Invalid regex: {detail}. This tool uses RE2 syntax (ripgrep-style): "
                    f"backreferences and look-around are unsupported; use \\z (not \\Z) for "
                    f"end-of-input."
                ),
            )

        # ─── 单 artifact 模式 ──────────────────────────────────────
        if aid is not None:
            result = await self._manager.read_artifact(
                session_id=session_id, artifact_id=aid, version=None
            )
            if result is None:
                return ToolResult(success=False, error=f"Artifact '{aid}' not found")

            content = result.get("content", "") or ""
            truncated = len(content) > config.GREP_CONTENT_MAX_CHARS
            if truncated:
                content = content[: config.GREP_CONTENT_MAX_CHARS]
            hits = _scan_content(content, regex, context_lines, max_count)
            match_count = sum(1 for _, _, is_match in hits if is_match)

            trunc_note = (
                f" (searched first {config.GREP_CONTENT_MAX_CHARS} chars only; "
                f"artifact is larger)"
                if truncated
                else ""
            )

            if match_count == 0:
                return ToolResult(success=True, data=f"No matches for {pattern!r}{trunc_note}")

            body = _format_flat(hits)
            data = f"{body}\n\n{match_count} matches{trunc_note}"
            return ToolResult(success=True, data=data)

        # ─── Session 模式（惰性逐 artifact 载入，GREP-02）──────────────
        # 只取 id 列表（include_content=False 不物化 content），循环内逐个
        # read_artifact 载入单份 → 扫描 → 下一轮覆盖释放，峰值内存收敛到单 artifact。
        # read_artifact 走 get_artifact，同样 cache-merged → in-memory 可见性不变。
        metas = await self._manager.list_artifacts(
            session_id=session_id, include_content=False
        )

        grouped: List[Tuple[str, List[Tuple[int, str, bool]]]] = []
        total_hits = 0
        scanned_chars = 0
        session_cap = config.SESSION_GREP_MAX_TOTAL
        scan_budget = config.GREP_SESSION_SCAN_BUDGET_CHARS
        budget_hit = False

        for meta in metas:
            remaining = session_cap - total_hits
            if remaining <= 0:
                break
            if scanned_chars >= scan_budget:
                budget_hit = True
                break

            loaded = await self._manager.read_artifact(
                session_id=session_id, artifact_id=meta["id"], version=None
            )
            if loaded is None:
                continue
            content = loaded.get("content", "") or ""
            if not content:
                continue

            # 单 artifact 截断 + 聚合预算双重收口
            if len(content) > config.GREP_CONTENT_MAX_CHARS:
                content = content[: config.GREP_CONTENT_MAX_CHARS]
            allowed = scan_budget - scanned_chars
            if len(content) > allowed:
                content = content[:allowed]
                budget_hit = True
            scanned_chars += len(content)

            effective_cap = min(max_count, remaining)
            hits = _scan_content(content, regex, context_lines, effective_cap)
            match_count = sum(1 for _, _, is_match in hits if is_match)
            if match_count == 0:
                continue

            grouped.append((meta["id"], hits))
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
        elif budget_hit:
            summary += (
                ". Hit scan budget — not all artifacts fully searched. "
                "Pass id=... to target a specific artifact."
            )

        data = f"{body}\n\n{summary}"
        return ToolResult(success=True, data=data)
