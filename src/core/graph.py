"""
可扩展的Graph构建器（重构版）
核心改进：
1. 简化agent_node逻辑
2. 简化route_func逻辑（基于phase）
3. user_confirmation_node支持任何agent
"""

from typing import Dict, Optional, Any, Callable
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from core.state import AgentState, ExecutionPhase, merge_agent_response_to_state
from core.context_manager import ContextManager
from agents.base import BaseAgent, AgentResponse
from tools.base import ToolResult
from utils.logger import get_logger

logger = get_logger("Core")


class ExtendableGraph:
    """
    可扩展的Graph构建器
    支持动态注册Agent和权限确认
    """
    
    def __init__(self):
        """初始化Graph构建器"""
        self.workflow = StateGraph(AgentState)
        self.agents: Dict[str, BaseAgent] = {}
        
        # 添加核心节点（权限确认）
        self._add_confirmation_node()
        
        logger.info("ExtendableGraph initialized")
    
    def _add_confirmation_node(self):
        """添加权限确认节点"""
        
        async def user_confirmation_node(state: AgentState) -> AgentState:
            """
            权限确认节点（支持任何agent）
            
            工作流程：
            1. 从permission_pending读取待确认信息
            2. 使用interrupt()请求用户确认
            3. 执行或拒绝工具
            4. 保存工具结果到permission_pending
            5. 设置phase返回原agent
            """
            logger.info("Entering user_confirmation_node")
            
            pending = state.get("permission_pending")
            if not pending:
                logger.error("No permission_pending found")
                state["phase"] = ExecutionPhase.COMPLETED
                return state
            
            from_agent = pending["from_agent"]
            tool_name = pending["tool_name"]
            params = pending["params"]
            permission_level = pending["permission_level"]
            
            logger.info(f"Requesting permission for '{tool_name}' from {from_agent}")
            
            # 请求用户确认
            is_approved = interrupt({
                "type": "tool_permission",
                "agent": from_agent,
                "tool_name": tool_name,
                "params": params,
                "permission_level": permission_level,
                "message": f"Tool '{tool_name}' requires {permission_level} permission"
            })
            
            # 执行或拒绝工具
            if is_approved:
                logger.info(f"Permission approved for '{tool_name}'")
                agent = self.agents.get(from_agent)
                if agent and agent.toolkit:
                    tool_result = await agent.toolkit.execute_tool(tool_name, params)
                else:
                    tool_result = ToolResult(
                        success=False,
                        error=f"Agent '{from_agent}' or toolkit not available"
                    )
            else:
                logger.info(f"Permission denied for '{tool_name}'")
                tool_result = ToolResult(
                    success=False,
                    error="Permission denied by user"
                )
            
            # 保存工具结果到permission_pending
            pending["tool_result"] = tool_result
            
            # 设置phase：返回原agent继续执行
            if from_agent == "lead_agent":
                state["phase"] = ExecutionPhase.LEAD_EXECUTING
            else:
                state["phase"] = ExecutionPhase.SUBAGENT_EXECUTING
            
            logger.info(f"Returning to {from_agent} after permission resolution")
            
            return state
        
        # 注册节点
        self.workflow.add_node("user_confirmation", user_confirmation_node)
    
    def register_agent(self, agent: BaseAgent) -> None:
        """
        注册Agent到Graph
        
        Args:
            agent: BaseAgent实例
        """
        agent_name = agent.config.name
        
        # 保存Agent实例
        self.agents[agent_name] = agent
        
        # 创建节点函数
        node_func = self._create_agent_node(agent_name)
        
        # 添加到workflow
        self.workflow.add_node(agent_name, node_func)
        
        # 添加路由规则
        self._add_routing_rules(agent_name)
        
        logger.info(f"✅ Registered agent: {agent_name}")
    
    def _create_agent_node(self, agent_name: str) -> Callable:
        """
        为Agent创建节点函数（简化版）
        
        Args:
            agent_name: Agent名称
            
        Returns:
            异步节点函数
        """
        async def agent_node(state: AgentState) -> AgentState:
            """Agent执行节点"""
            logger.info(f"Executing {agent_name} node")
            
            agent = self.agents[agent_name]
            memory = state.get("agent_memories", {}).get(agent_name, {})
            
            try:
                # ========== 准备执行参数 ==========
                routing_context = ContextManager.prepare_routing_context(agent_name, state)
                
                # 确定instruction
                if agent_name == "lead_agent":
                    instruction = state["current_task"]
                else:
                    # Subagent从subagent_route获取instruction
                    instruction = state.get("subagent_route", {}).get("instruction", "")
                
                execute_kwargs = {
                    "instruction": instruction,
                    "context": routing_context,
                }
                
                # ========== 检查是否从权限恢复 ==========
                is_resuming = False
                pending = state.get("permission_pending")
                
                if pending and pending.get("from_agent") == agent_name and pending.get("tool_result"):
                    # 从权限中断恢复
                    is_resuming = True
                    logger.info(f"{agent_name} resuming after permission")
                    
                    # 添加历史交互
                    execute_kwargs["tool_interactions"] = memory.get("tool_interactions", [])
                    
                    # 压缩处理（如果需要）
                    compression_level = state.get("compression_level", "normal")
                    if ContextManager.should_compress(execute_kwargs["tool_interactions"]):
                        execute_kwargs["tool_interactions"] = ContextManager.compress_messages(
                            execute_kwargs["tool_interactions"],
                            level=compression_level
                        )
                    
                    # 添加待处理的工具结果
                    tool_name = pending["tool_name"]
                    tool_result = pending["tool_result"]
                    execute_kwargs["pending_tool_result"] = (tool_name, tool_result)
                
                # ========== 执行Agent ==========
                response = await agent.execute(**execute_kwargs)
                
                # ========== 更新状态 ==========
                merge_agent_response_to_state(
                    state,
                    agent_name,
                    response,
                    is_resuming=is_resuming
                )
                
            except Exception as e:
                logger.exception(f"Error in {agent_name}: {e}")
                
                # 发生异常，设置为完成状态
                error_response = AgentResponse(
                    success=False,
                    content=f"Error in {agent_name}: {str(e)}",
                    metadata={'error': str(e)}
                )
                
                merge_agent_response_to_state(state, agent_name, error_response)
                state["phase"] = ExecutionPhase.COMPLETED
            
            return state
        
        return agent_node
    
    def _add_routing_rules(self, agent_name: str) -> None:
        """
        为Agent添加路由规则
        
        Args:
            agent_name: Agent名称
        """
        def route_func(state: AgentState) -> str:
            """
            基于phase的简化路由逻辑
            
            路由规则：
            1. WAITING_PERMISSION → user_confirmation
            2. SUBAGENT_EXECUTING → 目标subagent
            3. LEAD_EXECUTING → lead_agent（如果current_agent不是lead）
            4. COMPLETED → END
            """
            phase = state["phase"]
            current_agent = state.get("current_agent")
            
            # 1. 权限确认（优先级最高）
            if phase == ExecutionPhase.WAITING_PERMISSION:
                return "user_confirmation"
            
            # 2. Subagent执行
            elif phase == ExecutionPhase.SUBAGENT_EXECUTING:
                target = state["subagent_route"]["target"]
                return target
            
            # 3. Lead执行
            elif phase == ExecutionPhase.LEAD_EXECUTING:
                # 如果current_agent不是lead，说明需要返回lead
                if current_agent != "lead_agent":
                    return "lead_agent"
                # 否则不应该到这里（merge会设置其他phase）
                return END
            
            # 4. 完成
            elif phase == ExecutionPhase.COMPLETED:
                return END
            
            else:
                logger.error(f"Unexpected routing in phase: {phase}")
                return END
        
        # 构建路由映射（包含所有可能的目标）
        route_map = {
            "user_confirmation": "user_confirmation",
            "lead_agent": "lead_agent",
            END: END
        }
        
        # 添加所有已注册的agent
        for registered_agent in self.agents.keys():
            route_map[registered_agent] = registered_agent
        
        # 添加条件边
        self.workflow.add_conditional_edges(
            agent_name,
            route_func,
            route_map
        )
    
    def set_entry_point(self, agent_name: str = "lead_agent") -> None:
        """设置入口点"""
        self.workflow.set_entry_point(agent_name)
        logger.info(f"Entry point set to {agent_name}")
    
    def compile(
        self,
        checkpointer: Optional[Any] = None,
        interrupt_before: Optional[list] = None
    ) -> Any:
        """
        编译Graph
        
        Args:
            checkpointer: 检查点管理器
            interrupt_before: 中断点列表
            
        Returns:
            编译后的Graph
        """
        if checkpointer is None:
            checkpointer = MemorySaver()
        
        if interrupt_before is None:
            interrupt_before = []
        
        compiled = self.workflow.compile(
            checkpointer=checkpointer,
            interrupt_before=interrupt_before
        )
        
        logger.info(f"Graph compiled with {len(self.agents)} agents")
        return compiled


def create_multi_agent_graph():
    """
    创建多Agent Graph的工厂函数
    
    Returns:
        编译后的Graph
    """
    from agents.lead_agent import create_lead_agent, SubAgent
    from agents.search_agent import create_search_agent
    from agents.crawl_agent import create_crawl_agent
    from tools.registry import ToolRegistry
    from tools.implementations.artifact_ops import (
        CreateArtifactTool, UpdateArtifactTool,
        RewriteArtifactTool, ReadArtifactTool
    )
    from tools.implementations.call_subagent import CallSubagentTool
    from tools.implementations.web_search import WebSearchTool
    from tools.implementations.web_fetch import WebFetchTool
    
    # 创建Graph构建器
    graph_builder = ExtendableGraph()
    
    # 创建工具注册中心
    registry = ToolRegistry()
    
    # 注册所有工具
    for tool in [
        CreateArtifactTool(), UpdateArtifactTool(),
        RewriteArtifactTool(), ReadArtifactTool(),
        CallSubagentTool(), WebSearchTool(), WebFetchTool()
    ]:
        registry.register_tool_to_library(tool)
    
    # 创建Agent工具包
    lead_toolkit = registry.create_agent_toolkit(
        "lead_agent",
        tool_names=["create_artifact", "update_artifact", "rewrite_artifact",
                   "read_artifact", "call_subagent"]
    )
    
    search_toolkit = registry.create_agent_toolkit(
        "search_agent",
        tool_names=["web_search"]
    )
    
    crawl_toolkit = registry.create_agent_toolkit(
        "crawl_agent",
        tool_names=["web_fetch"]
    )
    
    # 创建Agent
    lead = create_lead_agent(lead_toolkit)
    search = create_search_agent(search_toolkit)
    crawl = create_crawl_agent(crawl_toolkit)
    
    # 注册子Agent到Lead
    lead.register_subagent(SubAgent(
        name="search_agent",
        description="Web search specialist",
        capabilities=["Web search", "Information retrieval"]
    ))
    lead.register_subagent(SubAgent(
        name="crawl_agent",
        description="Web content extraction specialist",
        capabilities=["Deep content extraction", "Web scraping", "IMPORTANT: Instructions must include a specific URL to crawl"]
    ))
    
    # 注册到Graph（顺序重要：先注册subagent）
    graph_builder.register_agent(search)
    graph_builder.register_agent(crawl)
    graph_builder.register_agent(lead)
    
    # 设置入口点
    graph_builder.set_entry_point("lead_agent")
    
    # 编译
    return graph_builder.compile()