"""
LangGraph状态定义
管理Multi-Agent系统的共享状态
"""

from typing import TypedDict, List, Dict, Optional, Annotated, Any
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    LangGraph的状态定义
    
    使用TypedDict定义状态结构，确保类型安全
    """
    # 核心消息历史（使用reducer函数管理）
    messages: Annotated[List[Dict], add_messages]
    
    # 任务管理
    current_task: str  # 当前任务描述
    session_id: Optional[str]  # 会话ID
    thread_id: Optional[str]  # 线程ID（用于checkpoint）
    
    # Agent路由控制
    next_agent: Optional[str]  # 下一个要执行的agent
    last_agent: Optional[str]  # 上一个执行的agent
    current_agent: Optional[str]  # 当前正在执行的agent
    
    # 工具权限控制
    pending_confirmation: Optional[Dict[str, Any]]  # 待确认的工具调用
    tool_confirmation: Optional[Dict[str, Any]]  # 工具确认结果
    
    # Artifacts管理
    task_plan_id: Optional[str]  # 任务计划artifact ID
    result_artifact_ids: List[str]  # 结果artifact ID列表
    artifacts_created: List[Dict[str, str]]  # 已创建的artifacts信息
    
    # 执行控制
    execution_status: str  # "running", "paused", "completed", "failed"
    interrupt_before: Optional[str]  # 在哪个节点前中断
    
    # 错误处理
    last_error: Optional[str]  # 最后的错误信息
    error_count: int  # 错误计数
    
    # Context管理
    context_level: str  # "full", "normal", "compact", "minimal"
    total_tokens_used: int  # 总token使用量
    
    # 元数据
    metadata: Dict[str, Any]  # 额外的元数据存储


def create_initial_state(
    task: str,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    context_level: str = "normal"
) -> Dict:
    """
    创建初始状态
    
    Args:
        task: 任务描述
        session_id: 会话ID
        thread_id: 线程ID
        context_level: 上下文级别
        
    Returns:
        初始化的状态字典
    """
    return {
        "messages": [],
        "current_task": task,
        "session_id": session_id or "",
        "thread_id": thread_id or "",
        "next_agent": "lead_agent",  # 默认从lead_agent开始
        "last_agent": None,
        "current_agent": None,
        "pending_confirmation": None,
        "tool_confirmation": None,
        "task_plan_id": None,
        "result_artifact_ids": [],
        "artifacts_created": [],
        "execution_status": "running",
        "interrupt_before": None,
        "last_error": None,
        "error_count": 0,
        "context_level": context_level,
        "total_tokens_used": 0,
        "metadata": {}
    }


def update_state_for_routing(
    state: AgentState,
    target_agent: str,
    from_agent: str
) -> None:
    """
    更新状态以进行agent路由
    
    Args:
        state: 当前状态
        target_agent: 目标agent
        from_agent: 来源agent
    """
    state["last_agent"] = from_agent
    state["next_agent"] = target_agent
    state["current_agent"] = None


def update_state_for_confirmation(
    state: AgentState,
    tool_name: str,
    params: Dict,
    permission_level: str
) -> None:
    """
    更新状态以进行工具确认
    
    Args:
        state: 当前状态
        tool_name: 工具名称
        params: 工具参数
        permission_level: 权限级别
    """
    state["pending_confirmation"] = {
        "tool_name": tool_name,
        "params": params,
        "permission_level": permission_level,
        "from_agent": state.get("current_agent"),
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }
    state["execution_status"] = "paused"
    state["interrupt_before"] = "tool_execution"


def extract_routing_from_response(response: Dict) -> Optional[Dict]:
    """
    从Agent响应中提取路由信息
    
    Args:
        response: Agent响应
        
    Returns:
        路由信息字典或None
    """
    if not response:
        return None
    
    # 检查routing字段
    if "routing" in response and response["routing"]:
        return response["routing"]
    
    # 检查tool_calls中的路由信息
    for tool_call in response.get("tool_calls", []):
        result = tool_call.get("result", {})
        if result.get("success"):
            data = result.get("data", {})
            if data.get("_is_routing_instruction"):
                return {
                    "target": data.get("_route_to"),
                    "instruction": data.get("instruction"),
                    "from_agent": response.get("metadata", {}).get("agent")
                }
    
    return None


def extract_confirmation_from_response(response: Dict) -> Optional[Dict]:
    """
    从Agent响应中提取权限确认需求
    
    Args:
        response: Agent响应
        
    Returns:
        确认信息字典或None
    """
    if not response:
        return None
    
    # 检查pending_confirmation字段
    if "pending_confirmation" in response and response["pending_confirmation"]:
        return response["pending_confirmation"]
    
    # 检查tool_calls中的确认需求
    for tool_call in response.get("tool_calls", []):
        result = tool_call.get("result", {})
        data = result.get("data", {})
        # 确保data是字典类型
        if isinstance(data, dict) and data.get("_needs_confirmation"):
            return {
                "tool_name": data.get("_tool_name"),
                "params": data.get("_params"),
                "permission_level": data.get("_permission_level"),
                "reason": data.get("_reason")
            }
    
    return None