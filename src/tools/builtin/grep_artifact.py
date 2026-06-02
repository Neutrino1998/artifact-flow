"""
Grep across artifact content with ripgrep-faithful semantics.

设计要点（详见 plan/CLAUDE.md "Minimize tool parameter surface"）:
- 单 / session 范围由 `id` 是否传入区分（对应 `rg pattern path` vs `rg pattern`）
- pattern 默认 **RE2 regex**（ripgrep / Rust-regex 同族：线性时间、无回溯 → 结构性免疫
  ReDoS）；`fixed_strings=true` 走 `re2.escape` 切 literal。换 RE2 是这个工具的本意
  —— 它自称 "ripgrep 语义"，而 ripgrep 底层正是不回溯、无 backref 的自动机引擎；旧的
  Python `re` 才是会被 `(a+)+$` 卡死事件循环的那个（2026-05-14 事故同源失败模式）。
- 资源护栏（line-oriented best-effort 搜索：定死**输入/输出 envelope**，envelope 内
  全物化才安全，超出即截断 + surface；不为对抗性巨输入逐 pass 补 cap）：
  - **输入** `GREP_CONTENT_MAX_CHARS`（2MB）—— 值由"`_scan_content` 的 pre-scan
    物化（splitlines×2 + line_starts，成本 O(行数)）保持有界"反推，**非** artifact
    最大尺寸；20MB 会 ~1GB（reviewer P1）。`GREP_SESSION_SCAN_BUDGET_CHARS` 限 session
    总扫描功。
  - **迭代** `GREP_MAX_SCAN_MATCHES`（raw-match 迭代上界，防"单行海量命中把 finditer
    抽干"，mirror update_artifact 的 `MAX_UNIQUE_CENTERS`）。
  - **输出** `GREP_MAX_LINE_CHARS`（ripgrep `--max-columns` 式单行封顶，防"单条巨行
    命中→整行塞进结果"，reviewer P2）。
  全部是 **CPU/扫描护栏**，不是内存护栏。RE2 线性故**无需墙钟 timeout**（回溯型才需，
  见 update_artifact）。session **峰值内存 ≈ 全 session content（有意 best-effort）**：
  内存由"载入多少"决定（list eager-load + cache 累积），不是扫描护栏能管的；真 bound
  需 repo 列投影 + 绕 cache，对内存从未爆过的 🟡 不划算。
- 部分搜索必 surface：任何 budget / 截断 / raw-cap 触发 → 结果带"search incomplete"
  提示，**绝不返回确定性的 No matches 误导模型**。
- 参数表面只暴露模型有语义意图的项；护栏常量全隐藏在 config
- 输出对齐 ripgrep TTY 行为：单 flat、session heading；命中行 `:`、context 行 `-`、
  group 间 `--`
"""

import asyncio
import bisect
from typing import TYPE_CHECKING, List, Optional, Tuple

import re2

from config import config
from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult

if TYPE_CHECKING:
    from tools.builtin.artifact_service import ArtifactService


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
    stats: Optional[dict] = None,
    max_scan: Optional[int] = None,
) -> List[Tuple[int, str, bool]]:
    """对整 artifact 跑 `regex.finditer(content)`，再把 match.start() 映射回行号。

    - 全文匹配（不是逐行）兑现 description 的 RE2/ripgrep 契约:
      `\\A`/`\\z` 真正指 artifact 边界、`foo\\nbar` 这类跨行 pattern 也能命中
    - 跨行 match 只在**起始行**打点（ripgrep `-U` 多行模式行为，避免一次匹配同时
      标记多行带来命中计数歧义）
    - 同一行多次命中去重（按行算 1 个命中）
    - **零宽匹配整体 drop** —— `\\A` / `^` / `\\b` / `\\z` / `^$` 等无可见内容的
      命中不进结果。这避免了"从源码字符串启发式判 anchor 意图"的不可靠路径
      (引擎没暴露 AST,任何 string-level 判断都有 false positive)。
      若需"找被空行分隔的段落",改用内容侧锚点(例:markdown 的 `^# `、小说的
      `^第.*章`、Python 的 `^class `),都是非零宽 pattern,正常工作。
    - max_count 限制 **行级命中** 数（去重后）；context 行不计入
    - **raw-match 迭代上界**（算法上界，Finding 1）:`max_count` 只数去重后的**行**，
      单行海量命中（如 `"a"*20M` 配 `a`）会全 collapse 到一行 → `max_count` 的 break
      永不触发 → `finditer` 被抽干（纯同步 CPU 钉死 GIL，2026-05-14 wedge 的另一个轴）。
      这里 cap 真正烧 CPU 的量——迭代到的**原始**命中数（非去重行数）。mirror
      update_artifact 的 `MAX_UNIQUE_CENTERS`。提前触顶即 break，置 `stats["scan_capped"]=True`。
      上界来源:`max_scan` 显式传入（session 模式传**剩余**预算，使 raw 预算跨 artifact
      累计共享 —— 否则每个 artifact 重置会累积无界 wedge，reviewer round 4）；不传则用
      `config.GREP_MAX_SCAN_MATCHES`（单 artifact 模式 = 整 budget）。

    返回 [(line_no_1indexed, line_text, is_match), ...]:
    - is_match=True 是命中行；False 是 context 行
    - 相邻命中的 context 重叠 → sliding skip 合并去重

    `stats`（可选 out-param）：若提供，写入 `stats["raw_scanned"]`（本次迭代的原始命中
    数，供 session 累计预算）；提前触顶 raw-match 上界时另置 `stats["scan_capped"]=True`。
    不提供（默认）则行为不变，纯函数单测无需感知。
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
    cap = max_scan if max_scan is not None else config.GREP_MAX_SCAN_MATCHES
    raw_seen = 0
    for m in regex.finditer(content):
        # raw-match 迭代上界:数的是**原始**命中(含零宽/去重前),这才是 finditer
        # 的真实迭代成本。触顶即停 —— max_count 只数去重行,单行海量命中时拦不住。
        if raw_seen >= cap:
            if stats is not None:
                stats["scan_capped"] = True
            break
        raw_seen += 1
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

    if stats is not None:
        stats["raw_scanned"] = raw_seen

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


def _truncate_line(text: str) -> str:
    """单行输出截断（ripgrep `--max-columns` 式）。挡"单条巨行命中 → 整行原样塞进
    ToolResult"——5M 字符的一行会先构造成 5M 的 body 再交给引擎落盘中间件,且若落盘
    fail-open 会直接进 SSE。超 `GREP_MAX_LINE_CHARS` 即截断 + 标记总长（命中可能在
    截断点之后,标记提示模型可 refine）。"""
    cap = config.GREP_MAX_LINE_CHARS
    if len(text) <= cap:
        return text
    return f"{text[:cap]} …[line truncated to {cap} of {len(text)} chars]"


def _format_flat(hits: List[Tuple[int, str, bool]]) -> str:
    """单 artifact 输出格式。命中行 `N:content`，context 行 `N-content`，
    相邻行号间断 → 插入 `--`（ripgrep 行为）。每行经 `_truncate_line` 封顶。

    空 hits 返回空串（调用方应在调用前判 No-matches，不应进到这里）。
    """
    out: List[str] = []
    prev_lineno: Optional[int] = None
    for lineno, text, is_match in hits:
        if prev_lineno is not None and lineno > prev_lineno + 1:
            out.append("--")
        sep = ":" if is_match else "-"
        out.append(f"{lineno}{sep}{_truncate_line(text)}")
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

    def __init__(self, service: "Optional[ArtifactService]" = None):
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
        self._service = service

    def set_service(self, service: "ArtifactService") -> None:
        """依赖注入入口（跟其他 artifact 工具一致）。"""
        self._service = service

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
        if not self._service:
            return ToolResult(success=False, error="ArtifactService not configured")

        session_id = self._service.current_session_id
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
            result = await self._service.read_artifact(
                session_id=session_id, artifact_id=aid, version=None
            )
            if result is None:
                return ToolResult(success=False, error=f"Artifact '{aid}' not found")

            content = result.get("content", "") or ""
            truncated = len(content) > config.GREP_CONTENT_MAX_CHARS
            if truncated:
                content = content[: config.GREP_CONTENT_MAX_CHARS]
            stats: dict = {}
            hits = _scan_content(content, regex, context_lines, max_count, stats)
            match_count = sum(1 for _, _, is_match in hits if is_match)

            # 任一护栏触发 → 搜的是部分内容，必须 surface（绝不发确定性 No matches）
            partial = truncated or stats.get("scan_capped", False)
            note = (
                " (search incomplete — scan limits hit; results may be missing later matches)"
                if partial
                else ""
            )

            if match_count == 0:
                return ToolResult(success=True, data=f"No matches for {pattern!r}{note}")

            body = _format_flat(hits)
            data = f"{body}\n\n{match_count} matches{note}"
            return ToolResult(success=True, data=data)

        # ─── Session 模式 ──────────────────────────────────────────
        # 一次性载入(include_content=True)。**峰值内存 ≈ 全 session content,有意 best-effort**:
        # 内存由"载入多少"决定(repo.list_artifacts 是 select(Artifact),content 列 eager-load;
        # 且 get_artifact 会 cache),不是下面的 scan 护栏能管的 —— 那些限的是"扫多少"(CPU)。
        # 真 bound 内存需 repo 列投影 + 绕 cache,对内存从未爆过的 🟡 不划算(详见 GREP-02)。
        artifacts = await self._service.list_artifacts(
            session_id=session_id, include_content=True
        )

        grouped: List[Tuple[str, List[Tuple[int, str, bool]]]] = []
        total_hits = 0
        scanned_chars = 0
        raw_used = 0  # 跨 artifact 累计的原始命中数（raw-match 预算 per-tool-call 共享）
        session_cap = config.SESSION_GREP_MAX_TOTAL
        scan_budget = config.GREP_SESSION_SCAN_BUDGET_CHARS
        raw_budget = config.GREP_MAX_SCAN_MATCHES
        partial = False  # 任何 budget / 截断 / raw-cap → 搜的是部分内容,必须 surface

        for art in artifacts:
            # 每个 artifact 间让出事件循环:① 不 wedge(整个 session 扫描原本是一坨无
            # await 的同步 CPU,健康探针/其他 session 全饿死);② 恢复**外部可取消性** ——
            # task.cancel() 能在此 await 落点生效,正是 2026-05-14 lease-fencing 96 分钟
            # 打不动的那个缺失落点。配合下面 per-call 累计预算,把"跨 artifact 连续 wedge"
            # 拆成"每 artifact ≤~一个有界小块、之间可取消"。
            await asyncio.sleep(0)

            content = art.get("content", "") or ""
            if not content:
                continue

            remaining = session_cap - total_hits
            if remaining <= 0:
                break
            if scanned_chars >= scan_budget:
                partial = True
                break
            remaining_raw = raw_budget - raw_used
            if remaining_raw <= 0:  # 跨 artifact 累计的 raw 预算耗尽 → 停 + surface
                partial = True
                break

            # per-artifact + 聚合预算双重 CPU 护栏(截断"扫描量",非内存)
            if len(content) > config.GREP_CONTENT_MAX_CHARS:
                content = content[: config.GREP_CONTENT_MAX_CHARS]
                partial = True
            allowed = scan_budget - scanned_chars
            if len(content) > allowed:
                content = content[:allowed]
                partial = True
            scanned_chars += len(content)

            effective_cap = min(max_count, remaining)
            stats: dict = {}
            # max_scan=remaining_raw → raw 预算跨 artifact 累计共享,而非每个 artifact 重置
            hits = _scan_content(
                content, regex, context_lines, effective_cap, stats, max_scan=remaining_raw
            )
            raw_used += stats.get("raw_scanned", 0)
            if stats.get("scan_capped"):
                partial = True
            match_count = sum(1 for _, _, is_match in hits if is_match)
            if match_count == 0:
                continue

            grouped.append((art["id"], hits))
            total_hits += match_count

            if total_hits >= session_cap:
                break

        if not grouped:
            # Finding 3a:部分搜索时绝不发确定性 No matches —— 未搜的内容里可能有命中
            if partial:
                return ToolResult(
                    success=True,
                    data=(
                        f"No matches for {pattern!r} in the searched portion "
                        f"(search incomplete — scan limits hit, not all content searched; "
                        f"pass id=... to target a specific artifact)"
                    ),
                )
            return ToolResult(success=True, data=f"No matches for {pattern!r}")

        body = _format_heading(grouped)
        summary = f"{total_hits} matches across {len(grouped)} artifacts"
        if total_hits >= session_cap:
            summary += (
                f". Hit session cap ({session_cap}). "
                f"Refine pattern or pass id=... to narrow."
            )
        elif partial:
            summary += (
                ". Search incomplete — scan limits hit, not all content searched. "
                "Pass id=... to target a specific artifact."
            )

        data = f"{body}\n\n{summary}"
        return ToolResult(success=True, data=data)
