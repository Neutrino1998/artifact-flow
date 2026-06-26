"""
search_tools —— 渐进式披露的「按需补全描述」工具(B-3)。

deferred tool unit 在 `<available_tools>` 里只出索引行(成员 full_name,无 param
schema)。模型要调用前先用 `search_tools` 把完整 schema 取回来 —— 结果作 tool_result
进历史(被压缩则模型见索引行自己再 search,不维护已发现集,decision 2)。

渲染依赖 **per-agent 的 EffectiveToolset + 本 turn 的 tools 字典**,而进程级工具实例
不能持有 per-turn 状态(并发 turn 共享同一实例会串)。故真正的渲染逻辑是纯函数
`search_tools_result(query, effective_toolset, tools)`,由引擎特殊路由调用(仿
call_subagent);`SearchToolsTool` 类只提供注册名 / 描述 / 参数 schema / 等级。
"""

import math
from typing import Dict, List

from tools.base import BaseTool, ToolParameter, ToolPermission, ToolResult
from tools.xml_formatter import render_tool_docs

_SELECT_PREFIX = "select:"


class SearchToolsTool(BaseTool):
    """渐进式披露的工具检索器(引擎特殊路由,execute 不在主路径被调用)。"""

    def __init__(self):
        super().__init__(
            name="search_tools",
            description=(
                "Load the full parameter schemas for tools that <available_tools> lists "
                "by name only (deferred tool units). Call this BEFORE calling such a tool. "
                "Query forms: `select:full_name,full_name` to fetch exact tools by name, "
                "or a plain keyword to search tool names + descriptions. The returned tool "
                "docs are for this conversation — if they later scroll out of context, just "
                "search again."
            ),
            permission=ToolPermission.AUTO,
            # 结果就是工具描述本身,绝不再落盘为 artifact(那会循环)。引擎特殊路由本就
            # 绕开 _maybe_persist_tool_result,这里 inf 只是把意图写明。
            max_result_size_chars=math.inf,
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

    async def execute(self, **params) -> ToolResult:  # pragma: no cover - engine-routed
        return ToolResult(
            success=False,
            error=(
                "search_tools is engine-routed (rendering needs the per-agent toolset); "
                "this execute path should not be reached."
            ),
        )


def search_tools_result(
    query: str,
    effective_toolset,
    tools: Dict[str, BaseTool],
) -> ToolResult:
    """渲染当前 agent 可调集里匹配工具的完整 doc(纯函数,引擎调用)。

    过滤口径(reviewer P1):只在 **EffectiveToolset 可调集 ∩ tools** 内检索 —— 含
    enabled-but-deferred,排 disabled / absent / 未授;search_tools 自身不入结果。
    """
    query = (query or "").strip()
    if not query:
        return ToolResult(
            success=False,
            error="search_tools requires a non-empty 'query' (use `select:full_name` or a keyword).",
        )

    callable_names = [
        n for n in effective_toolset.names()
        if n in tools and n != "search_tools"
    ]

    if query.startswith(_SELECT_PREFIX):
        wanted = [s.strip() for s in query[len(_SELECT_PREFIX):].split(",") if s.strip()]
        callable_set = set(callable_names)
        matched = [n for n in wanted if n in callable_set]
        unknown = [n for n in wanted if n not in callable_set]
    else:
        kw = query.lower()
        matched = [
            n for n in callable_names
            if kw in n.lower() or kw in (tools[n].description or "").lower()
        ]
        unknown = []

    if not matched:
        available = ", ".join(sorted(callable_names)) or "(none)"
        return ToolResult(
            success=True,
            data=(
                f"No tools matched '{query}'. Tools you can call: {available}.\n"
                "Use `select:<full_name>` to fetch one by exact name."
            ),
        )

    body = render_tool_docs([tools[n] for n in matched])
    if unknown:
        body += (
            "\n\nNot found or not available to you (skipped): "
            + ", ".join(unknown)
        )
    return ToolResult(success=True, data=body)
