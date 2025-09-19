"""
Graph状态定义
包含AgentState和相关数据结构
"""

from typing import TypedDict, Dict, List, Optional, Any
from datetime import datetime


class NodeMemory(TypedDict):
    """单个节点的记忆"""
    initial_instruction: str           # 初始用户请求
    messages: List[Dict]               # LLM与工具交互历史(不含system)
    last_response: Optional[Dict]      # 最后的AgentResponse
    tool_rounds: int                   # 工具调用轮次


class AgentState(TypedDict):
    """
    LangGraph全局状态（可扩展）
    支持动态添加Agent
    """
    # 基础信息
    current_task: str                      # 当前任务
    session_id: str                        # 会话ID（对应artifact session）
    thread_id: str                         # 线程ID（LangGraph checkpoint）
    parent_thread_id: Optional[str]        # 分支父节点
    
    # Agent记忆（可扩展）
    agent_memories: Dict[str, NodeMemory]  # key: agent_name, value: memory
    
    # 路由控制
    next_agent: Optional[str]              # 下一个要执行的agent
    last_agent: Optional[str]              # 上一个执行的agent
    routing_info: Optional[Dict]           # 路由附加信息
    
    # 权限确认
    pending_tool_confirmation: Optional[Dict]  # 待确认的工具调用
    
    # Artifacts
    task_plan_id: Optional[str]            # 任务计划artifact ID
    result_artifact_ids: List[str]         # 结果artifact ID列表
    
    # Context管理
    compression_level: str                 # "full", "normal", "compact"
    
    # 用户对话层
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
        "session_id": session_id or str(uuid4()),
        "thread_id": thread_id or str(uuid4()),
        "parent_thread_id": parent_thread_id,
        "agent_memories": {},
        "next_agent": None,
        "last_agent": None,
        "routing_info": None,
        "pending_tool_confirmation": None,
        "task_plan_id": None,
        "result_artifact_ids": [],
        "compression_level": compression_level,
        "user_message_id": str(uuid4()),
        "graph_response": None
    }


def merge_agent_response_to_state(
    state: AgentState,
    agent_name: str,
    response: Any,  # AgentResponse
    instruction: str = ""
) -> None:
    """
    将Agent响应合并到状态中
    
    Args:
        state: 当前状态
        agent_name: Agent名称
        response: AgentResponse对象
        instruction: 初始指令
    """
    # 保存Agent记忆
    state["agent_memories"][agent_name] = {
        "initial_instruction": instruction or state["agent_memories"].get(
            agent_name, {}
        ).get("initial_instruction", ""),
        "messages": response.messages,
        "last_response": {
            "success": response.success,
            "content": response.content,
            "tool_calls": response.tool_calls,
            "metadata": response.metadata
        },
        "tool_rounds": response.metadata.get("tool_rounds", 0)
    }
    
    # 更新last_agent
    state["last_agent"] = agent_name
    
    # 处理路由
    if response.routing:
        routing = response.routing
        
        if routing.get("type") == "permission_confirmation":
            # 需要权限确认
            state["next_agent"] = "user_confirmation"
            state["pending_tool_confirmation"] = {
                "tool_name": routing.get("tool_name"),
                "params": routing.get("params"),
                "from_agent": agent_name,
                "permission_level": routing.get("permission_level")
            }
        elif routing.get("type") == "subagent":
            # 路由到子Agent
            state["next_agent"] = routing.get("target")
            state["routing_info"] = routing
        else:
            # 其他路由类型
            state["routing_info"] = routing