"""
Graph状态定义（重构版）
核心改进：
1. 引入ExecutionPhase明确执行阶段
2. 简化状态字段，移除混乱的routing_info/pending_result
3. 统一的状态更新函数
"""

from typing import TypedDict, Dict, List, Optional, Any
from enum import Enum
from datetime import datetime
from utils.logger import get_logger

logger = get_logger("Core")


class ExecutionPhase(str, Enum):
    """
    执行阶段枚举
    
    状态转换规则：
    - LEAD_EXECUTING → WAITING_PERMISSION（需要权限）
    - LEAD_EXECUTING → SUBAGENT_EXECUTING（调用subagent）
    - LEAD_EXECUTING → COMPLETED（任务完成）
    - SUBAGENT_EXECUTING → WAITING_PERMISSION（需要权限）
    - SUBAGENT_EXECUTING → LEAD_EXECUTING（subagent完成）
    - WAITING_PERMISSION → LEAD_EXECUTING（从lead的权限恢复）
    - WAITING_PERMISSION → SUBAGENT_EXECUTING（从subagent的权限恢复）
    """
    LEAD_EXECUTING = "lead_executing"
    SUBAGENT_EXECUTING = "subagent_executing"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"


class NodeMemory(TypedDict):
    """单个节点的记忆"""
    tool_interactions: List[Dict]      # assistant-tool交互历史
    last_response: Optional[Dict]      # 最后的AgentResponse
    metadata: Dict[str, Any]           # 元数据


class AgentState(TypedDict):
    """
    LangGraph全局状态（重构版）
    
    核心改进：
    - 用phase明确当前阶段
    - 用current_agent取代last_agent
    - 简化为subagent_route和permission_pending两个路由字段
    - 添加conversation_history支持多轮对话
    """
    
    # ========== 基础信息 ==========
    current_task: str                      # 当前任务
    session_id: str                        # Artifact会话ID
    thread_id: str                         # LangGraph线程ID
    
    # ========== 对话上下文 ==========
    conversation_history: Optional[str]    # 格式化的对话历史（从ConversationManager获取）
    
    # ========== 执行状态 ==========
    phase: ExecutionPhase                  # 当前执行阶段
    current_agent: str                     # 当前执行的agent
    
    # ========== 路由数据（按需存在） ==========
    subagent_pending: Optional[Dict]         # {"target": str, "instruction": str, "subagent_result": ToolResult}
    permission_pending: Optional[Dict]       # {"tool_name": str, "params": dict, "permission_level": str, "from_agent": str, "tool_result": ToolResult}
    
    # ========== Agent记忆 ==========
    agent_memories: Dict[str, NodeMemory]
    
    # ========== Context管理 ==========
    compression_level: str                 # 压缩级别
    
    # ========== 用户交互层 ==========
    user_message_id: str                   # 当前用户消息ID
    graph_response: Optional[str]          # Graph最终响应


class UserMessage(TypedDict):
    """用户消息节点（对话树节点）"""
    message_id: str
    parent_id: Optional[str]
    content: str
    thread_id: str
    timestamp: str
    graph_response: Optional[str]
    metadata: Dict[str, Any]


class ConversationTree(TypedDict):
    """用户对话树"""
    conversation_id: str
    branches: Dict[str, List[str]]         # parent_msg_id -> [child_msg_ids]
    messages: Dict[str, UserMessage]       # msg_id -> message
    active_branch: str                     # 当前活跃分支的叶子节点ID
    created_at: str
    updated_at: str


def create_initial_state(
    task: str,
    session_id: str,
    thread_id: str,
    message_id: str,
    conversation_history: Optional[str] = None,
    compression_level: str = "normal"
) -> AgentState:
    """
    创建初始状态
    
    Args:
        task: 用户任务
        session_id: 会话ID
        thread_id: 线程ID
        message_id: 消息ID
        conversation_history: 格式化的对话历史
        compression_level: 压缩级别
        
    Returns:
        初始化的AgentState
    """
    return {
        "current_task": task,
        "session_id": session_id,
        "thread_id": thread_id,
        "conversation_history": conversation_history,
        "phase": ExecutionPhase.LEAD_EXECUTING,
        "current_agent": "lead_agent",
        "subagent_pending": None,
        "permission_pending": None,
        "agent_memories": {},
        "compression_level": compression_level,
        "user_message_id": message_id,
        "graph_response": None
    }


def merge_agent_response_to_state(
    state: AgentState,
    agent_name: str,
    response: Any,  # AgentResponse
    is_resuming: bool = False
) -> None:
    """
    统一的状态更新函数（重构版）
    
    核心改进：
    - 直接设置phase，不做复杂验证
    - 清晰的字段职责划分
    - 支持任何agent的权限请求
    
    Args:
        state: 当前状态
        agent_name: Agent名称
        response: AgentResponse对象
        is_resuming: 是否从中断恢复
    """
    
    # ========== 1. 更新当前agent ==========
    state["current_agent"] = agent_name
    
    # ========== 2. 清理恢复状态 ==========
    if is_resuming and state.get("permission_pending"):
        # 恢复执行后清理permission_pending
        state["permission_pending"] = None
    
    # ========== 3. 更新agent记忆 ==========
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
        "reasoning": getattr(response, 'reasoning_content', None)
    }
    
    # 更新metadata
    memory["metadata"]["tool_rounds"] = response.metadata.get("tool_rounds", 0)
    memory["metadata"]["completed_at"] = datetime.now().isoformat()
    
    # 累积token使用量
    if hasattr(response, 'token_usage') and response.token_usage:
        existing_usage = memory["metadata"].get("token_usage", {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        })
        
        new_usage = response.token_usage
        memory["metadata"]["token_usage"] = {
            "input_tokens": existing_usage["input_tokens"] + new_usage.get("input_tokens", 0),
            "output_tokens": existing_usage["output_tokens"] + new_usage.get("output_tokens", 0),
            "total_tokens": existing_usage["total_tokens"] + new_usage.get("total_tokens", 0)
        }
    
    # 记录执行次数
    memory["metadata"]["execution_count"] = memory["metadata"].get("execution_count", 0) + 1
    
    # 保存回state
    state["agent_memories"][agent_name] = memory
    
    # ========== 4. 处理路由逻辑 ==========
    if response.routing:
        routing_type = response.routing.get("type")
        
        if routing_type == "permission_confirmation":
            # 需要权限确认
            state["phase"] = ExecutionPhase.WAITING_PERMISSION
            state["permission_pending"] = {
                "tool_name": response.routing["tool_name"],
                "params": response.routing["params"],
                "permission_level": response.routing["permission_level"],
                "from_agent": agent_name,  # 记录是哪个agent需要权限
                "tool_result": None  # 等待user_confirmation_node填充
            }
            state["subagent_pending"] = None  # 清理
            
            logger.info(f"{agent_name} requesting permission for '{response.routing['tool_name']}'")
            
        elif routing_type == "subagent":
            # 路由到subagent
            state["phase"] = ExecutionPhase.SUBAGENT_EXECUTING
            state["subagent_pending"] = {
                "target": response.routing["target"],
                "instruction": response.routing["instruction"],
                "subagent_result": None  # 等待subagent填充
            }
            state["permission_pending"] = None  # 清理
            
            logger.info(f"{agent_name} routing to {response.routing['target']}")
    
    else:
        # 没有路由
        if agent_name != "lead_agent":
            # Subagent完成，封装结果为ToolResult
            from tools.base import ToolResult
            
            tool_result = ToolResult(
                success=response.success,
                data=response.content,
                metadata={
                    "agent": agent_name,
                    "tool_calls": response.tool_calls,
                    "reasoning": response.reasoning_content
                }
            )
            
            # 保存到subagent_pending
            if state.get("subagent_pending"):
                state["subagent_pending"]["subagent_result"] = tool_result
            
            # 返回lead_agent
            state["phase"] = ExecutionPhase.LEAD_EXECUTING
            state["subagent_pending"] = None  # 清理
            
            logger.info(f"{agent_name} completed, returning to lead_agent")
            
        else:
            # Lead Agent完成
            if not is_resuming:
                # 初次完成
                state["phase"] = ExecutionPhase.COMPLETED
                state["graph_response"] = response.content
                logger.info("Lead agent completed task")
            else:
                # 从恢复后继续执行
                state["phase"] = ExecutionPhase.LEAD_EXECUTING
                logger.info("Lead agent resumed execution")