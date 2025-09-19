"""
可扩展的Graph构建器
支持动态注册Agent和统一的节点处理
"""

from typing import Dict, Optional, Any, Callable
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from core.state import AgentState, merge_agent_response_to_state
from core.context_manager import ContextManager
from agents.base import BaseAgent
from tools.base import ToolResult
from utils.logger import get_logger

logger = get_logger("Core")


class ExtendableGraph:
    """
    可扩展的Graph构建器
    支持动态注册Agent
    """
    
    def __init__(self):
        """初始化Graph构建器"""
        self.workflow = StateGraph(AgentState)
        self.agents: Dict[str, BaseAgent] = {}
        self.node_functions: Dict[str, Callable] = {}
        
        # 添加核心节点（user_confirmation是特殊节点）
        self._add_core_nodes()
        
        logger.info("ExtendableGraph initialized")
    
    def _add_core_nodes(self):
        """添加核心节点"""
        # 用户确认节点（特殊处理）
        async def user_confirmation_node(state: AgentState) -> AgentState:
            """
            用户确认节点
            这是一个interrupt point，实际处理由Controller完成
            """
            logger.info("User confirmation required")
            # Graph会在这里中断，等待Controller处理
            return state
        
        self.workflow.add_node("user_confirmation", user_confirmation_node)
        self.node_functions["user_confirmation"] = user_confirmation_node
    
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
        
        logger.info(f"✅ Registered agent: {agent_name}")
    
    def _create_node_function(self, agent_name: str) -> Callable:
        """
        为Agent创建通用节点函数
        
        Args:
            agent_name: Agent名称
            
        Returns:
            异步节点函数
        """
        async def agent_node(state: AgentState) -> AgentState:
            """通用Agent节点函数"""
            logger.info(f"Executing {agent_name} node")
            
            # 获取Agent实例
            agent = self.agents[agent_name]
            
            # 获取或初始化节点记忆
            memory = state.get("agent_memories", {}).get(agent_name)
            
            try:
                # 准备上下文
                context = ContextManager.prepare_context_for_agent(
                    agent_name, state
                )
                
                # 判断执行模式
                if (state.get("pending_tool_confirmation") and 
                    state.get("pending_tool_confirmation", {}).get("from_agent") == agent_name):
                    # 模式1: 恢复执行（从权限确认返回）
                    logger.info(f"{agent_name} resuming from permission confirmation")
                    
                    pending = state["pending_tool_confirmation"]
                    tool_name, tool_result = pending["result"]
                    
                    # 使用保存的历史恢复执行
                    saved_messages = memory["messages"] if memory else []
                    
                    # 如果需要压缩
                    if ContextManager.should_compress(saved_messages):
                        saved_messages = ContextManager.compress_messages(
                            saved_messages, 
                            level=state.get("compression_level", "normal")
                        )
                    
                    response = await agent.execute(
                        instruction="",  # 恢复时不需要新指令
                        context=context,
                        external_history=saved_messages,
                        pending_tool_result=(tool_name, tool_result)
                    )
                    
                    # 清除pending状态
                    state["pending_tool_confirmation"] = None
                    
                elif agent_name != "lead_agent" and state.get("routing_info"):
                    # 模式2: 被路由到的子Agent
                    instruction = state["routing_info"].get("instruction", "")
                    logger.info(f"{agent_name} executing routed task: {instruction[:100]}...")
                    
                    response = await agent.execute(instruction, context)
                    
                else:
                    # 模式3: 正常执行（Lead Agent或直接调用）
                    if agent_name == "lead_agent":
                        instruction = state["current_task"]
                    else:
                        instruction = state.get("routing_info", {}).get("instruction", state["current_task"])
                    
                    logger.info(f"{agent_name} executing task: {instruction[:100]}...")
                    response = await agent.execute(instruction, context)
                
                # 合并响应到状态
                merge_agent_response_to_state(
                    state, 
                    agent_name, 
                    response,
                    instruction if 'instruction' in locals() else ""
                )
                
                # 如果是Lead Agent，保存最终响应
                if agent_name == "lead_agent" and not response.routing:
                    state["graph_response"] = response.content
                
                logger.info(f"{agent_name} completed successfully")
                
            except Exception as e:
                logger.exception(f"Error in {agent_name} node: {e}")
                # 错误状态
                state["last_agent"] = agent_name
                state["next_agent"] = None
                state["graph_response"] = f"Error in {agent_name}: {str(e)}"
            
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
        # 包含所有已注册的agent + 特殊节点
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
        
        # 默认在user_confirmation前中断
        if interrupt_before is None:
            interrupt_before = ["user_confirmation"]
        
        # 编译
        compiled = self.workflow.compile(
            checkpointer=checkpointer,
            interrupt_before=interrupt_before
        )
        
        logger.info(f"Graph compiled with {len(self.agents)} agents")
        return compiled


# 工厂函数
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