"""
核心执行模块
提供执行引擎、控制器、上下文管理和事件系统
"""

# 事件系统
from .events import (
    StreamEventType,
    ExecutionEvent,
)

# 执行引擎（含 metrics 类型 + 执行状态）
from .engine import (
    execute_loop,
    create_initial_state,
    ExecutionMetrics,
    TokenUsage,
    create_initial_metrics,
    finalize_metrics,
    accumulate_token_usage,
)

# 上下文管理
from .context_manager import ContextManager

# 执行控制器
from .controller import ExecutionController

# 对话管理
from .conversation_manager import ConversationManager

__all__ = [
    # 事件系统
    "StreamEventType",
    "ExecutionEvent",
    # 执行引擎
    "execute_loop",
    "create_initial_state",
    "ExecutionMetrics",
    "TokenUsage",
    "create_initial_metrics",
    "finalize_metrics",
    "accumulate_token_usage",
    # 上下文管理
    "ContextManager",
    # 执行控制器
    "ExecutionController",
    # 对话管理
    "ConversationManager",
]
