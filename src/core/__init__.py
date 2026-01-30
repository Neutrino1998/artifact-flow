"""
Core模块
提供Graph、Controller、状态管理和事件定义

使用 __getattr__ 实现延迟导入，避免循环导入问题：
- agents.base → core.events (OK)
- core.graph → agents.base (如果 core 包先被导入会循环)
"""

from typing import TYPE_CHECKING

# 静态类型检查时提供完整导入
if TYPE_CHECKING:
    from .graph import (
        ExtendableGraph,
        create_multi_agent_graph,
        create_async_sqlite_checkpointer
    )
    from .controller import ExecutionController
    from .conversation_manager import ConversationManager
    from .state import (
        AgentState,
        ExecutionPhase,
        create_initial_state,
        merge_agent_response_to_state
    )
    from .context_manager import ContextManager
    from .events import (
        StreamEventType,
        StreamEvent,
        ExecutionMetrics,
        TokenUsage,
        AgentExecutionRecord,
        ToolCallRecord,
        create_initial_metrics,
        finalize_metrics,
        append_agent_execution,
        append_tool_call
    )


# 模块映射：属性名 -> (模块名, 属性名)
_MODULE_MAPPING = {
    # Graph
    "ExtendableGraph": (".graph", "ExtendableGraph"),
    "create_multi_agent_graph": (".graph", "create_multi_agent_graph"),
    "create_async_sqlite_checkpointer": (".graph", "create_async_sqlite_checkpointer"),

    # Controller
    "ExecutionController": (".controller", "ExecutionController"),
    "ConversationManager": (".conversation_manager", "ConversationManager"),

    # State
    "AgentState": (".state", "AgentState"),
    "ExecutionPhase": (".state", "ExecutionPhase"),
    "create_initial_state": (".state", "create_initial_state"),
    "merge_agent_response_to_state": (".state", "merge_agent_response_to_state"),

    # Context
    "ContextManager": (".context_manager", "ContextManager"),

    # Events & Metrics
    "StreamEventType": (".events", "StreamEventType"),
    "StreamEvent": (".events", "StreamEvent"),
    "ExecutionMetrics": (".events", "ExecutionMetrics"),
    "TokenUsage": (".events", "TokenUsage"),
    "AgentExecutionRecord": (".events", "AgentExecutionRecord"),
    "ToolCallRecord": (".events", "ToolCallRecord"),
    "create_initial_metrics": (".events", "create_initial_metrics"),
    "finalize_metrics": (".events", "finalize_metrics"),
    "append_agent_execution": (".events", "append_agent_execution"),
    "append_tool_call": (".events", "append_tool_call"),
}


def __getattr__(name: str):
    """延迟导入，避免循环导入"""
    if name in _MODULE_MAPPING:
        module_name, attr_name = _MODULE_MAPPING[name]
        import importlib
        module = importlib.import_module(module_name, __package__)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """支持 dir() 和自动补全"""
    return list(_MODULE_MAPPING.keys())


__all__ = list(_MODULE_MAPPING.keys())
