"""
可扩展的Graph构建器
支持动态注册Agent和独立的权限确认节点
"""

from typing import Dict, Optional, Any, Callable, Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt, Command

from core.state import AgentState, merge_agent_response_to_state
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
        self.node_functions: Dict[str, Callable] = {}
        
        # 添加核心节点
        self._add_core_nodes()
        
        logger.info("ExtendableGraph initialized")
    
    def _add_core_nodes(self):
        """添加核心节点（权限确认节点）"""
        
        async def user_confirmation_node(state: AgentState) -> Any:
            """
            用户权限确认节点（简化版）
            """
            logger.info("Entering user_confirmation_node")
            
            # 从pending_result获取待确认的信息
            pending = state.get("pending_result")
            if not pending or pending.get("type") != "tool_permission":
                logger.error("No pending tool permission found")
                state["next_agent"] = None
                return state
            
            agent_name = pending["from_agent"]
            tool_name = pending["data"]["tool_name"]
            params = pending["data"]["params"]
            permission_level = pending["data"]["permission_level"]
            
            logger.info(f"Requesting permission for tool '{tool_name}' from {agent_name}")
            
            # 请求用户确认
            is_approved = interrupt({
                "type": "tool_permission",
                "agent": agent_name,
                "tool_name": tool_name,
                "params": params,
                "permission_level": permission_level,
                "message": f"Tool '{tool_name}' requires {permission_level} permission"
            })
            
            # 执行或拒绝工具
            if is_approved:
                logger.info(f"Permission approved for tool '{tool_name}'")
                agent = self.agents.get(agent_name)
                if agent and agent.toolkit:
                    tool_result = await agent.toolkit.execute_tool(tool_name, params)
                else:
                    tool_result = ToolResult(success=False, error=f"Agent or toolkit not available")
            else:
                logger.info(f"Permission denied for tool '{tool_name}'")
                tool_result = ToolResult(success=False, error="Permission denied by user")
            
            # 更新结果并设置路由（这里是唯一需要直接修改state的地方）
            state["pending_result"]["data"]["result"] = tool_result
            state["next_agent"] = agent_name  # 返回原agent
            
            return state
        
        # 注册节点
        self.workflow.add_node("user_confirmation", user_confirmation_node)
        self.node_functions["user_confirmation"] = user_confirmation_node
        
        # 添加路由规则：确认后返回原agent
        def confirmation_router(state: AgentState) -> str:
            next_agent = state.get("next_agent")
            if next_agent:
                state["next_agent"] = None
                logger.info(f"Routing from user_confirmation to {next_agent}")
                return next_agent
            return END
        
        # 为user_confirmation添加动态路由
        # 注意：路由映射会在register_agent时更新
        self.workflow.add_conditional_edges(
            "user_confirmation",
            confirmation_router,
            {END: END}  # 初始只有END，会动态更新
        )
    
    def register_agent(self, agent: BaseAgent) -> None:
        """
        注册新Agent
        
        Args:
            agent: BaseAgent实例
        """
        agent_name = agent.config.name
        
        # 保存Agent实例
        self.agents[agent_name] = agent
        
        # 创建节点函数
        node_func = self._create_node_function(agent_name)
        self.node_functions[agent_name] = node_func
        
        # 添加到workflow
        self.workflow.add_node(agent_name, node_func)
        
        # 添加路由规则
        self._add_routing_rules(agent_name)
        
        # 更新user_confirmation的路由映射，使其能路由到新agent
        self._update_confirmation_routing()
        
        logger.info(f"✅ Registered agent: {agent_name}")
    
    def _create_node_function(self, agent_name: str) -> Callable:
        """
        为Agent创建节点函数（只负责执行，不负责状态更新）
        
        Args:
            agent_name: Agent名称
            
        Returns:
            异步节点函数
        """
        async def agent_node(state: AgentState) -> AgentState:
            """通用Agent节点函数 - 单一职责：执行Agent"""
            logger.info(f"Executing {agent_name} node")
            
            # 获取Agent实例
            agent = self.agents[agent_name]
            
            # 获取节点记忆
            memory = state.get("agent_memories", {}).get(agent_name, {})
            
            try:
                # ========== 准备执行参数 ==========
                routing_context = ContextManager.prepare_routing_context(agent_name, state)
                
                # 确定instruction
                if agent_name == "lead_agent":
                    instruction = state["current_task"]
                else:
                    # Sub-agent从routing_info获取指令
                    instruction = state.get("routing_info", {}).get("instruction", "")
                
                execute_kwargs = {
                    "instruction": instruction,
                    "context": routing_context,
                }
                
                # ========== 检查是否是恢复执行 ==========
                is_resuming = False
                pending = state.get("pending_result")
                if pending and pending.get("from_agent") == agent_name:
                    is_resuming = True
                    logger.info(f"{agent_name} resuming with pending result")
                    
                    # 添加历史交互记录
                    execute_kwargs["tool_interactions"] = memory.get("tool_interactions", [])
                    
                    # 压缩处理
                    compression_level = state.get("compression_level", "normal")
                    compression_threshold = ContextManager.COMPRESSION_LEVELS.get(compression_level, 40000)
                    if ContextManager.should_compress(
                        execute_kwargs["tool_interactions"], 
                        threshold=compression_threshold
                    ):
                        execute_kwargs["tool_interactions"] = ContextManager.compress_messages(
                            execute_kwargs["tool_interactions"], 
                            level=compression_level
                        )
                    
                    # 根据pending类型准备参数
                    result_type = pending["type"]
                    
                    if result_type == "tool_permission":
                        # 工具权限确认结果
                        tool_name = pending["data"]["tool_name"]
                        tool_result = pending["data"]["result"]
                        execute_kwargs["pending_tool_result"] = (tool_name, tool_result)
                        
                    elif result_type == "subagent_response":
                        # Sub-agent响应结果
                        subagent_name = pending["data"]["agent"]
                        subagent_content = pending["data"]["content"]
                        
                        # 封装为ToolResult对象
                        tool_result = ToolResult(
                            success=True,
                            data={"agent": subagent_name, "response": subagent_content}
                        )
                        execute_kwargs["pending_tool_result"] = ("call_subagent", tool_result)
                
                # ========== 执行Agent ==========
                response = await agent.execute(**execute_kwargs)
                
                # ========== 判断是否最终完成 ==========
                is_completing = (
                    agent_name == "lead_agent" and 
                    not response.routing # 只要没有路由就是完成
                )
                
                # ========== 统一状态更新 ==========
                merge_agent_response_to_state(
                    state, 
                    agent_name, 
                    response,
                    is_resuming=is_resuming,
                    is_completing=is_completing
                )
                
            except Exception as e:
                logger.exception(f"Error in {agent_name}: {e}")
                # 发生异常，记录错误并完成
                error_response = AgentResponse(
                    success=False,
                    content=f"Graph node error in executing {agent_name}: {str(e)}",
                    metadata={'error': str(e), 'error_type': type(e).__name__}
                )
                
                merge_agent_response_to_state(
                    state,
                    agent_name,
                    error_response,
                    is_resuming=is_resuming,
                    is_completing=True  # 错误时直接完成
                )
                
            return state
        
        return agent_node
    
    def _add_routing_rules(self, agent_name: str) -> None:
        """
        添加Agent的路由规则
        
        Args:
            agent_name: Agent名称
        """
        def route_func(state: AgentState) -> str:
            """通用路由函数"""
            # 检查是否有指定的下一个节点
            if state.get("next_agent"):
                next_node = state["next_agent"]
                # 清空以避免循环
                state["next_agent"] = None
                logger.info(f"Routing from {agent_name} to {next_node}")
                return next_node
            
            # 默认结束
            logger.info(f"{agent_name} complete, ending workflow")
            return END
        
        # 构建路由映射
        route_map = {
            "user_confirmation": "user_confirmation",
            END: END
        }
        
        # 添加所有已注册的Agent
        for registered_agent in self.agents.keys():
            route_map[registered_agent] = registered_agent
        
        # 添加条件边
        self.workflow.add_conditional_edges(
            agent_name,
            route_func,
            route_map
        )
    
    def _update_confirmation_routing(self):
        """
        更新user_confirmation节点的路由映射
        使其能够路由到所有已注册的agent
        """
        def confirmation_router(state: AgentState) -> str:
            next_agent = state.get("next_agent")
            if next_agent:
                state["next_agent"] = None
                logger.info(f"Routing from user_confirmation to {next_agent}")
                return next_agent
            return END
        
        # 构建包含所有agent的路由映射
        route_map = {END: END}
        for agent_name in self.agents.keys():
            route_map[agent_name] = agent_name
        
        # 重新设置条件边
        # 注意：LangGraph可能不支持动态更新边，这里假设支持
        # 如果不支持，需要在compile前完成所有注册
        try:
            # 尝试更新（如果LangGraph支持）
            self.workflow.add_conditional_edges(
                "user_confirmation",
                confirmation_router,
                route_map
            )
        except:
            # 如果不支持动态更新，至少记录警告
            logger.warning("Cannot update confirmation routing dynamically")
    
    def set_entry_point(self, agent_name: str = "lead_agent") -> None:
        """
        设置入口点
        
        Args:
            agent_name: 入口Agent名称
        """
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
        # 默认使用MemorySaver
        if checkpointer is None:
            checkpointer = MemorySaver()
        
        # 默认在user_confirmation节点中断
        # 注意：由于我们在节点内部使用interrupt()，不需要interrupt_before
        if interrupt_before is None:
            interrupt_before = []
        
        # 编译
        compiled = self.workflow.compile(
            checkpointer=checkpointer,
            interrupt_before=interrupt_before
        )
        
        logger.info(f"Graph compiled with {len(self.agents)} agents and confirmation node")
        return compiled


# 工厂函数保持不变
def create_multi_agent_graph() -> ExtendableGraph:
    """
    创建多Agent Graph的便捷函数
    
    Returns:
        配置好的ExtendableGraph
    """
    from agents.lead_agent import create_lead_agent
    from agents.search_agent import create_search_agent
    from agents.crawl_agent import create_crawl_agent
    from tools.registry import ToolRegistry
    
    # 创建Graph构建器
    graph_builder = ExtendableGraph()
    
    # 创建工具注册中心
    registry = ToolRegistry()
    
    # 注册工具（这里简化，实际应该从配置加载）
    from tools.implementations.artifact_ops import (
        CreateArtifactTool, UpdateArtifactTool,
        RewriteArtifactTool, ReadArtifactTool
    )
    from tools.implementations.call_subagent import CallSubagentTool
    from tools.implementations.web_search import WebSearchTool
    from tools.implementations.web_fetch import WebFetchTool
    
    # 注册所有工具到库
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
    
    # 创建并注册Agent
    lead = create_lead_agent(lead_toolkit)
    search = create_search_agent(search_toolkit)
    crawl = create_crawl_agent(crawl_toolkit)
    
    # 注册子Agent到Lead Agent
    from agents.lead_agent import SubAgent
    lead.register_subagent(SubAgent(
        name="search_agent",
        description="Web search specialist",
        capabilities=["Web search", "Information retrieval"]
    ))
    lead.register_subagent(SubAgent(
        name="crawl_agent",
        description="Web content extraction specialist",
        capabilities=["Deep content extraction", "Web scraping"]
    ))
    
    # 注册到Graph（顺序重要：先注册子Agent）
    graph_builder.register_agent(search)
    graph_builder.register_agent(crawl)
    graph_builder.register_agent(lead)
    
    # 设置入口点
    graph_builder.set_entry_point("lead_agent")
    
    return graph_builder