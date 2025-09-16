"""
LangGraph工作流定义
定义Multi-Agent系统的执行流程
"""

from typing import Dict, List, Any, Optional
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from core.state import (
    AgentState, 
    extract_routing_from_response,
    extract_confirmation_from_response,
    update_state_for_routing,
    update_state_for_confirmation
)
from agents.lead_agent import LeadAgent
from agents.search_agent import SearchAgent
from agents.crawl_agent import CrawlAgent
from utils.logger import get_logger

logger = get_logger("Core")


# ==================== Agent节点定义 ====================

async def lead_agent_node(state: AgentState) -> Dict:
    """Lead Agent节点"""
    logger.info("Executing Lead Agent node")
    
    # 获取或创建Lead Agent实例
    lead_agent = get_lead_agent()
    
    # 准备输入
    if state["messages"]:
        # 获取最后的用户消息或工具结果
        last_message = state["messages"][-1]
        user_input = last_message.get("content", "")
    else:
        # 使用当前任务作为输入
        user_input = state["current_task"]
    
    # 准备context
    context = {
        "session_id": state.get("session_id"),
        "artifacts_created": state.get("artifacts_created", []),
        "context_level": state.get("context_level", "normal")
    }
    
    # 执行Agent
    try:
        response = await lead_agent.execute(user_input, context)
        
        # 更新状态
        state["current_agent"] = "lead_agent"
        
        # 添加消息到历史
        state["messages"].append({
            "role": "assistant",
            "content": response.content,
            "metadata": {
                "agent": "lead_agent",
                "tool_calls": len(response.tool_calls),
                "has_routing": bool(response.routing)
            }
        })
        
        # 更新token使用量
        if response.token_usage:
            state["total_tokens_used"] += response.token_usage.get("total_tokens", 0)
        
        # 提取路由信息
        routing = extract_routing_from_response(response.__dict__)
        if routing:
            update_state_for_routing(state, routing["target"], "lead_agent")
            logger.info(f"Lead Agent routing to {routing['target']}")
        
        # 提取确认需求
        confirmation = extract_confirmation_from_response(response.__dict__)
        if confirmation:
            update_state_for_confirmation(
                state,
                confirmation["tool_name"],
                confirmation["params"],
                confirmation["permission_level"]
            )
            logger.info(f"Lead Agent requires confirmation for {confirmation['tool_name']}")
        
        # 更新artifacts信息
        for tool_call in response.tool_calls:
            if tool_call["tool"] in ["create_artifact", "update_artifact", "rewrite_artifact"]:
                result = tool_call.get("result", {})
                if result.get("success"):
                    artifact_info = {
                        "id": tool_call["params"].get("id"),
                        "type": tool_call["params"].get("content_type", "markdown"),
                        "title": tool_call["params"].get("title", "")
                    }
                    if artifact_info not in state["artifacts_created"]:
                        state["artifacts_created"].append(artifact_info)
        
        return state
        
    except Exception as e:
        logger.error(f"Lead Agent error: {e}")
        state["last_error"] = str(e)
        state["error_count"] += 1
        state["execution_status"] = "failed"
        return state


async def search_agent_node(state: AgentState) -> Dict:
    """Search Agent节点"""
    logger.info("Executing Search Agent node")
    
    # 获取或创建Search Agent实例
    search_agent = get_search_agent()
    
    # 准备输入（从路由信息或消息历史获取）
    if state["messages"]:
        last_message = state["messages"][-1]
        # 检查是否有路由指令
        if "instruction" in last_message.get("metadata", {}):
            user_input = last_message["metadata"]["instruction"]
        else:
            user_input = last_message.get("content", "")
    else:
        user_input = "Please search for relevant information"
    
    # 准备context
    context = {
        "session_id": state.get("session_id"),
        "context_level": state.get("context_level", "normal")
    }
    
    # 执行Agent
    try:
        response = await search_agent.execute(user_input, context)
        
        # 更新状态
        state["current_agent"] = "search_agent"
        state["last_agent"] = "search_agent"
        
        # 添加结果到消息历史
        state["messages"].append({
            "role": "tool",
            "content": f"<search_results>\n{response.content}\n</search_results>",
            "metadata": {
                "agent": "search_agent",
                "tool_calls": len(response.tool_calls)
            }
        })
        
        # 更新token使用量
        if response.token_usage:
            state["total_tokens_used"] += response.token_usage.get("total_tokens", 0)
        
        return state
        
    except Exception as e:
        logger.error(f"Search Agent error: {e}")
        state["last_error"] = str(e)
        state["error_count"] += 1
        return state


async def crawl_agent_node(state: AgentState) -> Dict:
    """Crawl Agent节点"""
    logger.info("Executing Crawl Agent node")
    
    # 获取或创建Crawl Agent实例
    crawl_agent = get_crawl_agent()
    
    # 准备输入
    if state["messages"]:
        last_message = state["messages"][-1]
        if "instruction" in last_message.get("metadata", {}):
            user_input = last_message["metadata"]["instruction"]
        else:
            user_input = last_message.get("content", "")
    else:
        user_input = "Please extract content from the specified URLs"
    
    # 准备context
    context = {
        "session_id": state.get("session_id"),
        "context_level": state.get("context_level", "normal")
    }
    
    # 执行Agent
    try:
        response = await crawl_agent.execute(user_input, context)
        
        # 更新状态
        state["current_agent"] = "crawl_agent"
        state["last_agent"] = "crawl_agent"
        
        # 添加结果到消息历史
        state["messages"].append({
            "role": "tool",
            "content": f"<crawl_results>\n{response.content}\n</crawl_results>",
            "metadata": {
                "agent": "crawl_agent",
                "tool_calls": len(response.tool_calls)
            }
        })
        
        # 更新token使用量
        if response.token_usage:
            state["total_tokens_used"] += response.token_usage.get("total_tokens", 0)
        
        return state
        
    except Exception as e:
        logger.error(f"Crawl Agent error: {e}")
        state["last_error"] = str(e)
        state["error_count"] += 1
        return state


async def user_confirmation_node(state: AgentState) -> Dict:
    """用户确认节点（占位符，实际需要外部处理）"""
    logger.info("User confirmation required")
    
    # 设置中断标记
    state["execution_status"] = "paused"
    state["interrupt_before"] = "tool_execution"
    
    # 这里会触发中断，等待外部输入
    # 实际的确认逻辑由ExecutionController处理
    
    return state


# ==================== 路由函数 ====================

def route_after_lead(state: AgentState) -> str:
    """Lead Agent之后的路由逻辑"""
    logger.debug("Routing after Lead Agent")
    
    # 检查错误
    if state.get("error_count", 0) >= 3:
        logger.warning("Too many errors, ending execution")
        return "end"
    
    # 检查是否需要确认
    if state.get("pending_confirmation"):
        logger.info("Routing to user confirmation")
        return "confirm"
    
    # 检查路由指令
    next_agent = state.get("next_agent")
    if next_agent == "search_agent":
        logger.info("Routing to Search Agent")
        return "search"
    elif next_agent == "crawl_agent":
        logger.info("Routing to Crawl Agent")
        return "crawl"
    
    # 检查是否完成
    if state.get("execution_status") == "completed":
        logger.info("Execution completed, ending")
        return "end"
    
    # 默认结束
    logger.info("No routing instruction, ending")
    return "end"


def route_after_subagent(state: AgentState) -> str:
    """Sub-agent之后的路由逻辑"""
    logger.debug("Routing after sub-agent")
    
    # Sub-agent执行后总是返回Lead Agent
    return "lead"


# ==================== Agent实例管理 ====================

# 全局Agent实例缓存
_agent_instances = {}

def get_lead_agent() -> LeadAgent:
    """获取Lead Agent实例（单例）"""
    if "lead_agent" not in _agent_instances:
        from tools.registry import ToolRegistry
        from tools.implementations.artifact_ops import (
            CreateArtifactTool, UpdateArtifactTool,
            RewriteArtifactTool, ReadArtifactTool
        )
        from tools.implementations.call_subagent import CallSubagentTool
        
        # 创建工具注册中心
        registry = ToolRegistry()
        
        # 注册工具
        registry.register_tool_to_library(CreateArtifactTool())
        registry.register_tool_to_library(UpdateArtifactTool())
        registry.register_tool_to_library(RewriteArtifactTool())
        registry.register_tool_to_library(ReadArtifactTool())
        registry.register_tool_to_library(CallSubagentTool())
        
        # 创建工具包
        toolkit = registry.create_agent_toolkit(
            "lead_agent",
            tool_names=[
                "create_artifact", "update_artifact",
                "rewrite_artifact", "read_artifact",
                "call_subagent"
            ]
        )
        
        # 创建Agent
        _agent_instances["lead_agent"] = LeadAgent(toolkit=toolkit)
        
        # 注册子Agent
        from agents.lead_agent import SubAgent
        lead = _agent_instances["lead_agent"]
        lead.register_subagent(SubAgent(
            name="search_agent",
            description="Searches the web for information",
            capabilities=["Web search", "Information retrieval"]
        ))
        lead.register_subagent(SubAgent(
            name="crawl_agent",
            description="Extracts content from web pages",
            capabilities=["Content extraction", "Web crawling"]
        ))
    
    return _agent_instances["lead_agent"]


def get_search_agent() -> SearchAgent:
    """获取Search Agent实例（单例）"""
    if "search_agent" not in _agent_instances:
        from tools.registry import ToolRegistry
        from tools.implementations.web_search import WebSearchTool
        
        registry = ToolRegistry()
        registry.register_tool_to_library(WebSearchTool())
        
        toolkit = registry.create_agent_toolkit(
            "search_agent",
            tool_names=["web_search"]
        )
        
        _agent_instances["search_agent"] = SearchAgent(toolkit=toolkit)
    
    return _agent_instances["search_agent"]


def get_crawl_agent() -> CrawlAgent:
    """获取Crawl Agent实例（单例）"""
    if "crawl_agent" not in _agent_instances:
        from tools.registry import ToolRegistry
        from tools.implementations.web_fetch import WebFetchTool
        
        registry = ToolRegistry()
        registry.register_tool_to_library(WebFetchTool())
        
        toolkit = registry.create_agent_toolkit(
            "crawl_agent",
            tool_names=["web_fetch"]
        )
        
        _agent_instances["crawl_agent"] = CrawlAgent(toolkit=toolkit)
    
    return _agent_instances["crawl_agent"]


# ==================== Graph构建函数 ====================

def create_simple_graph():
    """创建最简单的工作流：Lead → (Search/Crawl) → Lead → END"""
    logger.info("Creating simple graph")
    
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("lead_agent", lead_agent_node)
    workflow.add_node("search_agent", search_agent_node)
    workflow.add_node("crawl_agent", crawl_agent_node)
    
    # 设置入口
    workflow.set_entry_point("lead_agent")
    
    # 添加条件边
    workflow.add_conditional_edges(
        "lead_agent",
        route_after_lead,
        {
            "search": "search_agent",
            "crawl": "crawl_agent",
            "end": END
        }
    )
    
    # Sub-agent返回到Lead
    workflow.add_edge("search_agent", "lead_agent")
    workflow.add_edge("crawl_agent", "lead_agent")
    
    # 编译
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def create_graph_with_confirmation():
    """创建带权限确认的工作流"""
    logger.info("Creating graph with confirmation")
    
    workflow = StateGraph(AgentState)
    
    # 添加所有节点
    workflow.add_node("lead_agent", lead_agent_node)
    workflow.add_node("search_agent", search_agent_node)
    workflow.add_node("crawl_agent", crawl_agent_node)
    workflow.add_node("user_confirmation", user_confirmation_node)
    
    # 设置入口
    workflow.set_entry_point("lead_agent")
    
    # Lead Agent的条件路由
    workflow.add_conditional_edges(
        "lead_agent",
        route_after_lead,
        {
            "search": "search_agent",
            "crawl": "crawl_agent",
            "confirm": "user_confirmation",
            "end": END
        }
    )
    
    # Sub-agent返回到Lead
    workflow.add_edge("search_agent", "lead_agent")
    workflow.add_edge("crawl_agent", "lead_agent")
    
    # 确认节点中断并返回Lead
    workflow.add_edge("user_confirmation", "lead_agent")
    
    # 编译
    checkpointer = MemorySaver()
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["user_confirmation"]  # 在确认节点前中断
    )


# 默认使用简单图
def create_default_graph():
    """创建默认的工作流（带确认）"""
    return create_graph_with_confirmation()