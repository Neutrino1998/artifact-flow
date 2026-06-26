"""
search_tools —— 渐进式披露的「按需补全描述」工具(B-3)。

deferred tool unit 在 `<available_tools>` 里只出索引行(成员 full_name,无 param
schema)。模型要调用前先用 `search_tools` 把完整 schema 取回来 —— 结果作 tool_result
进历史(被压缩则模型见索引行自己再 search,不维护已发现集,decision 2)。

渲染依赖 **per-agent 的 EffectiveToolset + 本 turn 的 tools 字典**。该工具 `wants_context`
= True:引擎在正常工具路径里注入 `ToolExecutionContext`,故 search_tools 走**正常路由**
(白嫖 validate_params / 正常事件 / 可取消 / 落盘安全网),不是引擎特殊分支;进程级实例
仍无状态(context 调用时注入)。
"""

from typing import Dict, List, Optional

from config import config
from tools.base import (
    SEARCH_TOOLS_NAME,
    BaseTool,
    ToolExecutionContext,
    ToolParameter,
    ToolPermission,
    ToolResult,
)
from tools.xml_formatter import render_tool_docs

_SELECT_PREFIX = "select:"


class SearchToolsTool(BaseTool):
    """渐进式披露的工具检索器(wants_context,走正常工具路由)。"""

    wants_context = True

    def __init__(self):
        super().__init__(
            name=SEARCH_TOOLS_NAME,
            description=(
                "Load the full parameter schemas for tools that <available_tools> lists "
                "by name only (deferred tool units). Call this BEFORE calling such a tool. "
                "Query forms: `select:full_name,full_name` to fetch exact tools by name, "
                "or a plain keyword to search tool names + descriptions. The returned tool "
                "docs are for this conversation — if they later scroll out of context, just "
                "search again."
            ),
            permission=ToolPermission.AUTO,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description=(
                    "`select:full_name,full_name` for exact tools by name, or a keyword "
                    "to search tool names and descriptions."
                ),
                required=True,
            )
        ]

    async def execute(self, _context: Optional[ToolExecutionContext] = None, **params) -> ToolResult:
        if _context is None:
            # wants_context 工具必由引擎注入 context;缺席 = 引擎接线 bug,响亮报错。
            return ToolResult(
                success=False,
                error="search_tools requires engine context but none was injected (engine wiring bug).",
            )
        return search_tools_result(
            params.get("query", ""), _context.effective_toolset, _context.tools
        )


def search_tools_result(
    query: str,
    effective_toolset,
    tools: Dict[str, BaseTool],
) -> ToolResult:
    """渲染当前 agent 可调集里匹配工具的完整 doc(纯函数,execute / 测试共用)。

    过滤口径(reviewer P1):只在 **EffectiveToolset 可调集 ∩ tools** 内检索 —— 含
    enabled-but-deferred,排 disabled / absent / 未授;search_tools 自身不入结果。
    输出有界:匹配数封顶 `SEARCH_TOOLS_MAX_RESULTS`,超出只渲前 N + 列其余名(防把整集
    schema 灌爆下一次 call —— 压缩不兜底 tool-result overflow,工具自负输出大小)。
    """
    query = (query or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error="search_tools requires a non-empty 'query' (use `select:full_name` or a keyword).",
        )

    callable_names = [
        n for n in effective_toolset.names()
        if n in tools and n != SEARCH_TOOLS_NAME
    ]

    if query.startswith(_SELECT_PREFIX):
        callable_set = set(callable_names)
        matched: List[str] = []
        unknown: List[str] = []
        seen: set = set()
        for raw in query[len(_SELECT_PREFIX):].split(","):
            name = raw.strip()
            if not name or name in seen:
                continue  # 空段 / 重复名去重保序
            seen.add(name)
            (matched if name in callable_set else unknown).append(name)
    else:
        kw = query.lower()
        matched = [
            n for n in callable_names
            if kw in n.lower() or kw in (tools[n].description or "").lower()
        ]
        unknown = []

    if not matched:
        available = ", ".join(sorted(callable_names)) or "(none)"
        msg = f"No tools matched '{query}'. Tools you can call: {available}."
        if unknown:
            msg += "\nNot found or not available to you: " + ", ".join(unknown)
        msg += "\nUse `select:<full_name>` to fetch one by exact name."
        return ToolResult(success=True, data=msg)

    # 输出上限:超过封顶则只渲前 N、其余列名(模型可缩小 query / 改 select 精确取)。
    cap = config.SEARCH_TOOLS_MAX_RESULTS
    overflow = matched[cap:]
    shown = matched[:cap]

    body = render_tool_docs([tools[n] for n in shown])
    notes = []
    if overflow:
        notes.append(
            f"{len(overflow)} more tool(s) matched but were omitted (cap {cap}): "
            + ", ".join(overflow)
            + ". Narrow your query or use `select:<full_name>` to fetch specific ones."
        )
    if unknown:
        notes.append("Not found or not available to you (skipped): " + ", ".join(unknown))
    if notes:
        body += "\n\n" + "\n".join(notes)
    return ToolResult(success=True, data=body)
