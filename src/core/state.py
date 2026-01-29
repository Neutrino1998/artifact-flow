"""
Graph状态定义
核心改进：
1. 引入ExecutionPhase明确执行阶段
2. 简化状态字段，移除混乱的routing_info/pending_result
3. 统一的状态更新函数
4. 新增 ExecutionMetrics 可观测性字段
"""

from typing import TypedDict, Dict, List, Optional, Any
from enum import Enum
from datetime import datetime
from utils.logger import get_logger
from core.events import ExecutionMetrics, create_initial_metrics

logger = get_logger("ArtifactFlow")


class ExecutionPhase(str, Enum):
    """
    执行阶段枚举

    状态转换规则：
    - LEAD_EXECUTING → TOOL_EXECUTING（有工具调用）
    - LEAD_EXECUTING → SUBAGENT_EXECUTING（调用subagent）
    - LEAD_EXECUTING → COMPLETED（任务完成）
    - SUBAGENT_EXECUTING → TOOL_EXECUTING（有工具调用）
    - SUBAGENT_EXECUTING → LEAD_EXECUTING（subagent完成）
    - TOOL_EXECUTING → WAITING_PERMISSION（需要确认）
    - TOOL_EXECUTING → LEAD_EXECUTING/SUBAGENT_EXECUTING（执行完成，返回原agent）
    - WAITING_PERMISSION → LEAD_EXECUTING/SUBAGENT_EXECUTING（确认后返回原agent）
    """
    LEAD_EXECUTING = "lead_executing"
    SUBAGENT_EXECUTING = "subagent_executing"
    TOOL_EXECUTING = "tool_executing"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"


class NodeMemory(TypedDict):
    """单个节点的记忆"""
    tool_interactions: List[Dict]      # assistant-tool交互历史
    last_response: Optional[Dict]      # 最后的AgentResponse
    metadata: Dict[str, Any]           # 元数据
    tool_round_count: int              # 当前节点工具调用计数


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
    conversation_history: Optional[List[Dict]]    # 格式化的对话历史（从ConversationManager获取）
    
    # ========== 执行状态 ==========
    phase: ExecutionPhase                  # 当前执行阶段
    current_agent: str                     # 当前执行的agent
    
    # ========== 路由数据（按需存在） ==========
    subagent_pending: Optional[Dict]         # {"target": str, "instruction": str, "subagent_result": ToolResult}
    pending_tool_call: Optional[Dict]        # {"tool_name": str, "params": dict, "from_agent": str, "tool_result": ToolResult}
    
    # ========== Agent记忆 ==========
    agent_memories: Dict[str, NodeMemory]

    # ========== Context管理 ==========
    compression_level: str                 # 压缩级别

    # ========== 用户交互层 ==========
    user_message_id: str                   # 当前用户消息ID
    graph_response: Optional[str]          # Graph最终响应

    # ========== 可观测性 ==========
    execution_metrics: ExecutionMetrics    # 执行指标（token使用、工具调用、耗时等）


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
    conversation_history: Optional[List[Dict]] = None,
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
        "pending_tool_call": None,
        "agent_memories": {},
        "compression_level": compression_level,
        "user_message_id": message_id,
        "graph_response": None,
        "execution_metrics": create_initial_metrics()
    }


def merge_agent_response_to_state(
    state: AgentState,
    agent_name: str,
    response: Any,  # AgentResponse
    is_resuming: bool = False
) -> None:
    """
    统一的状态更新函数

    处理 Agent 执行后的状态更新和路由：
    - tool_call → TOOL_EXECUTING，设置 pending_tool_call
    - subagent → SUBAGENT_EXECUTING，设置 subagent_pending
    - 无路由 → COMPLETED（lead）或返回 LEAD_EXECUTING（subagent）

    Args:
        state: 当前状态
        agent_name: Agent名称
        response: AgentResponse对象
        is_resuming: 是否从工具执行恢复
    """

    # ========== 1. 更新当前agent ==========
    state["current_agent"] = agent_name

    # ========== 2. 清理恢复状态 ==========
    if is_resuming:
        if state.get("pending_tool_call"):
            state["pending_tool_call"] = None
            logger.debug(f"{agent_name} resumed from tool execution, clearing pending state")
        if agent_name == "lead_agent" and state.get("subagent_pending"):
            state["subagent_pending"] = None
            logger.debug(f"{agent_name} resumed from subagent, clearing pending state")

    # ========== 3. 更新agent记忆 ==========
    memory = state["agent_memories"].get(agent_name, {
        "tool_interactions": [],
        "last_response": None,
        "metadata": {},
        "tool_round_count": 0
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
    memory["metadata"]["completed_at"] = datetime.now().isoformat()

    # 记录执行次数
    memory["metadata"]["execution_count"] = memory["metadata"].get("execution_count", 0) + 1

    # 注意：token 统计已移至 execution_metrics，由 graph 层的 agent_node 负责追加

    # 保存回state
    state["agent_memories"][agent_name] = memory

    # ========== 4. 处理路由逻辑 ==========
    if response.routing:
        routing_type = response.routing.get("type")

        if routing_type == "tool_call":
            # 路由到工具执行节点
            state["phase"] = ExecutionPhase.TOOL_EXECUTING
            state["pending_tool_call"] = {
                "tool_name": response.routing["tool_name"],
                "params": response.routing["params"],
                "from_agent": agent_name,
                "tool_result": None  # 等待 tool_execution_node 填充
            }
            # 增加工具轮数计数
            memory["tool_round_count"] = memory.get("tool_round_count", 0) + 1
            logger.info(f"{agent_name} requesting tool '{response.routing['tool_name']}' (round {memory['tool_round_count']})")

        elif routing_type == "subagent":
            # 路由到subagent（清零当前agent的tool_round_count）
            memory["tool_round_count"] = 0
            state["phase"] = ExecutionPhase.SUBAGENT_EXECUTING
            state["subagent_pending"] = {
                "target": response.routing["target"],
                "instruction": response.routing["instruction"],
                "subagent_result": None  # 等待subagent填充
            }
            logger.info(f"{agent_name} routing to {response.routing['target']}")

    else:
        # 没有路由 → Agent 完成
        # 清零 tool_round_count
        memory["tool_round_count"] = 0

        # 检查是否执行失败
        if not response.success:
            # Agent执行失败 → 终止执行
            state["phase"] = ExecutionPhase.COMPLETED
            state["graph_response"] = response.content
            logger.error(f"{agent_name} failed, terminating execution: {response.content}")
            return

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
            logger.info(f"{agent_name} completed, returning to lead_agent")

        else:
            # Lead agent完成 → 任务结束
            state["phase"] = ExecutionPhase.COMPLETED
            state["graph_response"] = response.content
            logger.info("Lead agent completed task")