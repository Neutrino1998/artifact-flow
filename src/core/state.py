"""
Graph状态定义
包含AgentState和相关数据结构
"""

from typing import TypedDict, Dict, List, Optional, Any
from datetime import datetime
from utils.logger import get_logger

logger = get_logger("Core")

class NodeMemory(TypedDict):
    """单个节点的记忆"""
    tool_interactions: List[Dict]      # 只包含assistant-tool交互历史
    last_response: Optional[Dict]      # 最后的AgentResponse（用于调试/展示）
    metadata: Dict[str, Any]           # 元数据：tool_rounds, completed_at, token_usage, execution_count


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
    compression_level: str = "normal",
    message_id: Optional[str] = None
) -> AgentState:
    """
    创建初始状态的辅助函数
    
    Args:
        task: 用户任务
        session_id: 会话ID
        thread_id: 线程ID
        parent_thread_id: 父线程ID（用于分支）
        compression_level: 压缩级别
        message_id: 关联的用户消息ID
        
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
        "compression_level": compression_level,
        "user_message_id": message_id or f"msg-{uuid4()}",
        "graph_response": None
    }



def merge_agent_response_to_state(
    state: AgentState,
    agent_name: str,
    response: Any,  # AgentResponse
    is_resuming: bool = False,
    is_completing: bool = False
) -> None:
    """
    统一的状态更新函数 - 所有状态修改都在这里进行
    
    Args:
        state: 当前状态
        agent_name: Agent名称
        response: AgentResponse对象
        is_resuming: 是否是恢复执行（需要清理pending_result）
        is_completing: 是否是最终完成（Lead Agent需要设置graph_response）
    """
    # ========== 1. 清理恢复执行的pending状态 ==========
    if is_resuming and state.get("pending_result"):
        if state["pending_result"].get("from_agent") == agent_name:
            logger.debug(f"Clearing pending_result for {agent_name} after resume")
            state["pending_result"] = None
    
    # ========== 2. 更新agent记忆 ==========
    memory = state["agent_memories"].get(agent_name, {
        "tool_interactions": [],
        "last_response": None,
        "metadata": {}
    })
    
    # 合并工具交互历史
    if hasattr(response, "tool_interactions") and response.tool_interactions:
        memory["tool_interactions"].extend(response.tool_interactions)
    
    # 更新最后的响应
    memory["last_response"] = {
        "success": response.success,
        "content": response.content,
        "tool_calls": response.tool_calls,
        "metadata": response.metadata,
        "reasoning": response.reasoning_content if hasattr(response, 'reasoning_content') else None
    }
    
    # ========== 更新metadata ==========
    # 保留已有的metadata，更新新的值
    if "metadata" not in memory:
        memory["metadata"] = {}
    
    # 更新工具轮次
    memory["metadata"]["tool_rounds"] = response.metadata.get("tool_rounds", 0)
    
    # 更新完成时间
    memory["metadata"]["completed_at"] = datetime.now().isoformat()
    
    # ✅ 累积token使用量
    if hasattr(response, 'token_usage') and response.token_usage:
        # 获取已有的token统计
        existing_usage = memory["metadata"].get("token_usage", {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        })
        
        # 累加新的使用量
        new_usage = response.token_usage
        memory["metadata"]["token_usage"] = {
            "input_tokens": existing_usage.get("input_tokens", 0) + new_usage.get("input_tokens", 0),
            "output_tokens": existing_usage.get("output_tokens", 0) + new_usage.get("output_tokens", 0),
            "total_tokens": existing_usage.get("total_tokens", 0) + new_usage.get("total_tokens", 0)
        }
        
        logger.debug(
            f"{agent_name} token usage - "
            f"Input: {new_usage.get('input_tokens', 0)} "
            f"(total: {memory['metadata']['token_usage']['input_tokens']}), "
            f"Output: {new_usage.get('output_tokens', 0)} "
            f"(total: {memory['metadata']['token_usage']['output_tokens']})"
        )
    
    # 记录执行次数（每次调用都+1）
    memory["metadata"]["execution_count"] = memory["metadata"].get("execution_count", 0) + 1
    
    # 保存回state
    state["agent_memories"][agent_name] = memory
    
    # ========== 3. 更新追踪字段 ==========
    state["last_agent"] = agent_name
    
    # ========== 4. 处理路由逻辑 ==========
    if response.routing:
        routing = response.routing
        routing_type = routing.get("type")
        
        if routing_type == "permission_confirmation":
            # 需要权限确认
            state["next_agent"] = "user_confirmation"
            state["pending_result"] = {
                "type": "tool_permission",
                "from_agent": agent_name,
                "data": {
                    "tool_name": routing.get("tool_name"),
                    "params": routing.get("params"),
                    "permission_level": routing.get("permission_level"),
                    "result": None
                }
            }
            logger.info(f"Setting up permission confirmation for {routing.get('tool_name')}")
            
        elif routing_type == "subagent":
            # 路由到子Agent
            state["next_agent"] = routing.get("target")
            state["routing_info"] = routing
            logger.info(f"Setting up routing to {routing.get('target')}")
            
    else:
        # 没有路由，清理routing_info
        state["routing_info"] = None
        
        # ========== 5. 处理完成场景 ==========
        if agent_name != "lead_agent":
            # Sub-agent完成，返回给Lead Agent
            state["pending_result"] = {
                "type": "subagent_response",
                "from_agent": "lead_agent",  # Lead Agent需要处理这个结果
                "data": {
                    "agent": agent_name,
                    "content": response.content,
                    "tool_calls": len(response.tool_calls) if response.tool_calls else 0
                }
            }
            state["next_agent"] = "lead_agent"
            logger.info(f"{agent_name} completed, setting up return to lead_agent")
            
        elif is_completing:
            # Lead Agent最终完成
            state["graph_response"] = response.content
            state["next_agent"] = None  # 结束工作流
            logger.info(f"Lead Agent completed with final response")