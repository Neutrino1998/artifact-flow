"""
核心执行模块
提供执行引擎、控制器、上下文管理和事件系统
"""

# 事件系统
from .events import (
    StreamEventType,
    ExecutionEvent,
    ExecutionMetrics,
    AgentExecutionRecord,
    ToolCallRecord,
    TokenUsage,
    create_initial_metrics,
    finalize_metrics,
    append_agent_execution,
    append_tool_call,
)

# 执行状态
from .state import create_initial_state

# 执行引擎
from .engine import execute_loop

# 上下文管理
from .context_manager import ContextManager, Context

# 执行控制器
from .controller import ExecutionController

# 对话管理
from .conversation_manager import ConversationManager

__all__ = [
    # 事件系统
    "StreamEventType",
    "ExecutionEvent",
    "ExecutionMetrics",
    "AgentExecutionRecord",
    "ToolCallRecord",
    "TokenUsage",
    "create_initial_metrics",
    "finalize_metrics",
    "append_agent_execution",
    "append_tool_call",
    # 执行状态
    "create_initial_state",
    # 执行引擎
    "execute_loop",
    # 上下文管理
    "ContextManager",
    "Context",
    # 执行控制器
    "ExecutionController",
    # 对话管理
    "ConversationManager",
]
