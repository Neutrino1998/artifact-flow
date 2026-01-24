"""
Core模块
提供Graph、Controller、状态管理和事件定义
"""

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


__all__ = [
    # Graph
    "ExtendableGraph",
    "create_multi_agent_graph",
    "create_async_sqlite_checkpointer",

    # Controller
    "ExecutionController",
    "ConversationManager",

    # State
    "AgentState",
    "ExecutionPhase",
    "create_initial_state",
    "merge_agent_response_to_state",

    # Context
    "ContextManager",

    # Events & Metrics
    "StreamEventType",
    "StreamEvent",
    "ExecutionMetrics",
    "TokenUsage",
    "AgentExecutionRecord",
    "ToolCallRecord",
    "create_initial_metrics",
    "finalize_metrics",
    "append_agent_execution",
    "append_tool_call",
]
