"""
Core模块
提供Graph、Controller和状态管理
"""

from core.graph import (
    ExtendableGraph,
    create_multi_agent_graph
)

from core.controller import (
    ExecutionController,
    ConversationManager
)

from core.state import (
    AgentState,
    ExecutionPhase,
    create_initial_state,
    merge_agent_response_to_state
)

from core.context_manager import ContextManager


__all__ = [
    # Graph
    "ExtendableGraph",
    "create_multi_agent_graph",
    
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
]