"""
Graph状态定义
包含AgentState和相关数据结构
"""

from typing import TypedDict, Dict, List, Optional, Any
from datetime import datetime


class NodeMemory(TypedDict):
    """单个节点的记忆"""
    tool_interactions: List[Dict]      # 只包含assistant-tool交互历史
    last_response: Optional[Dict]      # 最后的AgentResponse（用于调试/展示）
    tool_rounds: int                   # 工具调用轮次
    completed_at: Optional[str]        # 完成时间（用于调试）


class AgentState(TypedDict):
    """LangGraph全局状态"""
    # 基础信息
    current_task: str                      # 当前任务（始终保持）
    session_id: str                        # Artifact会话ID
    thread_id: str                         # LangGraph线程ID
    parent_thread_id: Optional[str]        # 父线程（用于分支）
    
    # Agent记忆（新结构）
    agent_memories: Dict[str, NodeMemory]  # 每个agent的记忆
    
    # 路由控制
    next_agent: Optional[str]              # 下一个要执行的agent
    last_agent: Optional[str]              # 上一个执行的agent
    routing_info: Optional[Dict]           # 路由信息（主要是instruction）
    
    # 统一的待处理结果
    pending_result: Optional[Dict]         
    # {
    #   "type": "tool_permission" | "subagent_response",
    #   "from_agent": str,  # 需要处理结果的agent
    #   "data": Any
    # }
    
    # Artifacts
    task_plan_id: Optional[str]            # 任务计划artifact ID
    result_artifact_ids: List[str]         # 结果artifact ID列表
    
    # Context管理
    compression_level: str                 # 压缩级别： "full", "normal", "compact"
    
    # 用户交互层
    user_message_id: str                   # 当前用户消息ID
    graph_response: Optional[str]          # Graph最终响应


class UserMessage(TypedDict):
    """用户消息节点（对话树节点）"""
    message_id: str
    parent_id: Optional[str]               # 父消息ID（用于分支）
    content: str                           # 用户消息内容
    thread_id: str                         # 关联的Graph执行线程
    timestamp: str                         # 时间戳
    graph_response: Optional[str]          # Graph的响应
    metadata: Dict[str, Any]               # 额外元数据


class ConversationTree(TypedDict):
    """用户对话树（Layer 1）"""
    conversation_id: str
    branches: Dict[str, List[str]]         # parent_msg_id -> [child_msg_ids]
    messages: Dict[str, UserMessage]       # msg_id -> message
    active_branch: str                     # 当前活跃分支的叶子节点ID
    created_at: str
    updated_at: str


def create_initial_state(
    task: str,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    parent_thread_id: Optional[str] = None,
    compression_level: str = "normal"
) -> AgentState:
    """
    创建初始状态的辅助函数
    
    Args:
        task: 用户任务
        session_id: 会话ID
        thread_id: 线程ID
        parent_thread_id: 父线程ID（用于分支）
        compression_level: 压缩级别
        
    Returns:
        初始化的AgentState
    """
    from uuid import uuid4
    
    return {
        "current_task": task,
        "session_id": session_id or f"sess-{uuid4()}",
        "thread_id": thread_id or f"thd-{uuid4()}",
        "parent_thread_id": parent_thread_id,
        "agent_memories": {},
        "next_agent": None,
        "last_agent": None,
        "routing_info": None,
        "pending_result": None,
        "task_plan_id": None,
        "result_artifact_ids": [],
        "compression_level": compression_level,
        "user_message_id": f"msg-{uuid4()}",
        "graph_response": None
    }


def merge_agent_response_to_state(
    state: AgentState,
    agent_name: str,
    response: Any,  # AgentResponse
) -> None:
    """
    将Agent响应合并到状态中
    
    Args:
        state: 当前状态
        agent_name: Agent名称
        response: AgentResponse对象
    """
    # 获取或创建agent记忆
    memory = state["agent_memories"].get(agent_name, {
        "tool_interactions": [],
        "last_response": None,
        "tool_rounds": 0,
        "completed_at": None
    })
    
    # 合并工具交互历史（追加新的交互）
    if hasattr(response, "tool_interactions"):
        memory["tool_interactions"].extend(response.tool_interactions)
    
    # 更新最后的响应
    memory["last_response"] = {
        "success": response.success,
        "content": response.content,
        "tool_calls": response.tool_calls,  # 用于展示
        "metadata": response.metadata,
        "reasoning": response.reasoning_content
    }
    
    # 更新轮次和时间
    memory["tool_rounds"] = response.metadata.get("tool_rounds", 0)
    memory["completed_at"] = datetime.now().isoformat()
    
    # 保存回state
    state["agent_memories"][agent_name] = memory
    
    # 更新last_agent
    state["last_agent"] = agent_name
    
    # 处理路由
    if response.routing:
        routing = response.routing
        
        if routing.get("type") == "permission_confirmation":
            # 需要权限确认
            state["next_agent"] = "user_confirmation"
            state["pending_result"] = {
                "type": "tool_permission",
                "from_agent": agent_name,
                "data": {
                    "tool_name": routing.get("tool_name"),
                    "params": routing.get("params"),
                    "permission_level": routing.get("permission_level"),
                    "result": None  # 将在权限确认后填充
                }
            }
            logger.info(f"Permission required for {routing.get('tool_name')}")
            
        elif routing.get("type") == "subagent":
            # 路由到子Agent
            state["next_agent"] = routing.get("target")
            state["routing_info"] = routing
            logger.info(f"Routing to {routing.get('target')}")
    else:
        # 没有路由，清理routing_info
        state["routing_info"] = None