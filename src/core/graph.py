"""
可扩展的Graph构建器（支持流式输出）

架构说明：
1. Agent 只做单轮 LLM 调用，工具执行由 Graph 控制
2. tool_execution_node 统一处理所有工具调用（PUBLIC/NOTIFY/CONFIRM/RESTRICTED）
3. 工具调用循环由 Graph 路由实现（agent → tool_exec → agent → ...）
4. 支持流式输出 (stream_mode="custom")
"""

from typing import Dict, Optional, Any, Callable, AsyncGenerator
from datetime import datetime
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, StreamWriter

from core.state import AgentState, ExecutionPhase, merge_agent_response_to_state
from core.context_manager import ContextManager
from core.events import (
    StreamEventType, StreamEvent,
    append_agent_execution, append_tool_call, TokenUsage
)
from agents.base import BaseAgent, AgentResponse
from tools.base import ToolPermission, ToolResult
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class ExtendableGraph:
    """
    可扩展的Graph构建器

    支持动态注册Agent，统一的工具执行节点
    支持流式输出 (stream_mode="custom")

    Workflow:
    [Start]
        → [agent_node] (单轮 LLM 调用)
            ├→ [TOOL_EXECUTING] → [tool_execution_node] → [agent_node] (继续)
            ├→ [SUBAGENT_EXECUTING] → [subagent_node] → [agent_node] (lead 恢复)
            └→ [COMPLETED] → [End]

    工具执行流程:
    [tool_execution_node]
        ├→ PUBLIC/NOTIFY → 直接执行 → 返回原 agent
        └→ CONFIRM/RESTRICTED → interrupt() → 确认后执行 → 返回原 agent
    """

    def __init__(self, artifact_manager: Optional["ArtifactManager"] = None):
        """
        初始化Graph构建器

        Args:
            artifact_manager: ArtifactManager 实例，用于在 agent 上下文中注入 artifacts 清单
        """
        self.workflow = StateGraph(AgentState)
        self.agents: Dict[str, BaseAgent] = {}
        self.artifact_manager = artifact_manager

        # 添加核心节点（工具执行）
        self._add_tool_execution_node()

        logger.info("ExtendableGraph initialized")
    
    def _add_tool_execution_node(self):
        """添加统一的工具执行节点"""

        async def tool_execution_node(state: AgentState, writer: StreamWriter) -> AgentState:
            """
            统一的工具执行节点

            处理两级权限的工具调用：
            - AUTO → 直接执行
            - CONFIRM → interrupt() 请求用户确认后执行

            工作流程：
            1. 从 pending_tool_call 读取待执行信息
            2. 获取工具并检查权限
            3. 根据权限级别决定是否需要确认（发送 PERMISSION_REQUEST/PERMISSION_RESULT）
            4. 执行工具（发送 TOOL_START/TOOL_COMPLETE）
            5. 更新 execution_metrics
            6. 保存结果到 pending_tool_call
            7. 设置 phase 返回原 agent
            """
            logger.info("Entering tool_execution_node")

            pending = state.get("pending_tool_call")
            if not pending:
                logger.error("No pending_tool_call found")
                state["phase"] = ExecutionPhase.COMPLETED
                return state

            from_agent = pending["from_agent"]
            tool_name = pending["tool_name"]
            params = pending["params"]

            logger.info(f"Executing tool '{tool_name}' for {from_agent}")

            # 获取 agent 和工具
            agent = self.agents.get(from_agent)
            if not agent or not agent.toolkit:
                tool_result = ToolResult(
                    success=False,
                    error=f"Agent '{from_agent}' or toolkit not available"
                )
                pending["tool_result"] = tool_result
                state["phase"] = self._get_return_phase(from_agent)
                return state

            tool = agent.toolkit.get_tool(tool_name)
            if not tool:
                tool_result = ToolResult(
                    success=False,
                    error=f"Tool '{tool_name}' not found"
                )
                pending["tool_result"] = tool_result
                state["phase"] = self._get_return_phase(from_agent)
                return state

            # 根据权限级别处理
            if tool.permission == ToolPermission.CONFIRM:
                # 需要用户确认
                logger.info(f"Tool '{tool_name}' requires {tool.permission.value} permission")

                # 发送 PERMISSION_REQUEST 事件
                writer({
                    "type": StreamEventType.PERMISSION_REQUEST.value,
                    "agent": from_agent,
                    "tool": tool_name,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "permission_level": tool.permission.value,
                        "params": params
                    }
                })

                is_approved = interrupt({
                    "type": "tool_permission",
                    "agent": from_agent,
                    "tool_name": tool_name,
                    "params": params,
                    "permission_level": tool.permission.value,
                    "message": f"Tool '{tool_name}' requires {tool.permission.value} permission"
                })

                # 发送 PERMISSION_RESULT 事件
                writer({
                    "type": StreamEventType.PERMISSION_RESULT.value,
                    "agent": from_agent,
                    "tool": tool_name,
                    "timestamp": datetime.now().isoformat(),
                    "data": {
                        "approved": is_approved
                    }
                })

                if not is_approved:
                    logger.info(f"Permission denied for '{tool_name}'")
                    tool_result = ToolResult(
                        success=False,
                        error="Permission denied by user. You do not have permission to use this tool."
                    )
                    pending["tool_result"] = tool_result
                    state["phase"] = self._get_return_phase(from_agent)
                    return state

                logger.info(f"Permission approved for '{tool_name}'")

            # 发送 TOOL_START 事件
            tool_start_time = datetime.now()
            writer({
                "type": StreamEventType.TOOL_START.value,
                "agent": from_agent,
                "tool": tool_name,
                "timestamp": tool_start_time.isoformat(),
                "data": {
                    "params": params
                }
            })

            # 执行工具（PUBLIC/NOTIFY 直接执行，CONFIRM/RESTRICTED 确认后执行）
            try:
                tool_result = await agent.toolkit.execute_tool(tool_name, params)
                logger.info(f"Tool '{tool_name}' executed: {'SUCCESS' if tool_result.success else 'FAILED'}")
            except Exception as e:
                logger.exception(f"Tool '{tool_name}' execution error: {e}")
                tool_result = ToolResult(
                    success=False,
                    error=str(e)
                )

            # 计算工具执行耗时
            tool_end_time = datetime.now()
            tool_duration_ms = int((tool_end_time - tool_start_time).total_seconds() * 1000)

            # 发送 TOOL_COMPLETE 事件
            writer({
                "type": StreamEventType.TOOL_COMPLETE.value,
                "agent": from_agent,
                "tool": tool_name,
                "timestamp": tool_end_time.isoformat(),
                "data": {
                    "success": tool_result.success,
                    "duration_ms": tool_duration_ms,
                    "error": tool_result.error if not tool_result.success else None
                }
            })

            # 更新 execution_metrics
            append_tool_call(
                metrics=state["execution_metrics"],
                tool_name=tool_name,
                success=tool_result.success,
                duration_ms=tool_duration_ms,
                called_at=tool_start_time.isoformat(),
                completed_at=tool_end_time.isoformat(),
                agent=from_agent
            )

            # 保存结果
            pending["tool_result"] = tool_result

            # 返回原 agent 继续
            state["phase"] = self._get_return_phase(from_agent)
            logger.info(f"Returning to {from_agent} after tool execution")

            return state

        # 注册节点
        self.workflow.add_node("tool_execution", tool_execution_node)

    def _get_return_phase(self, agent_name: str) -> ExecutionPhase:
        """根据 agent 名称获取返回的 phase"""
        if agent_name == "lead_agent":
            return ExecutionPhase.LEAD_EXECUTING
        else:
            return ExecutionPhase.SUBAGENT_EXECUTING
    
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
        
        logger.info(f"Registered agent: {agent_name}")
    
    def _create_agent_node(self, agent_name: str) -> Callable:
        """
        为Agent创建节点函数（支持流式输出）
        
        Args:
            agent_name: Agent名称
            
        Returns:
            异步节点函数
        """
        async def agent_node(state: AgentState, writer: StreamWriter) -> AgentState:
            """
            Agent执行节点（单轮 LLM 调用，流式版本）

            职责：
            1. 准备消息（包括工具结果恢复）
            2. 执行单轮 LLM 调用
            3. 更新状态和路由
            """
            logger.info(f"Executing {agent_name} node (streaming)")

            agent = self.agents[agent_name]
            memory = state.get("agent_memories", {}).get(agent_name, {})

            # 检查是否达到工具轮数上限
            tool_round_count = memory.get("tool_round_count", 0)
            max_tool_rounds = agent.config.max_tool_rounds
            hit_tool_limit = tool_round_count >= max_tool_rounds

            if hit_tool_limit:
                logger.warning(f"{agent_name} reached max tool rounds ({max_tool_rounds})")

            try:
                # ========== 准备执行参数 ==========
                # 确定instruction
                if agent_name == "lead_agent":
                    instruction = state["current_task"]
                else:
                    # Subagent从subagent_pending获取lead agent instruction
                    instruction = state.get("subagent_pending", {}).get("instruction", "")

                # 检查是否从中断恢复
                tool_interactions = None
                pending_tool_result = None
                is_resuming = False

                # 1. 检查工具执行恢复
                if pending := state.get("pending_tool_call"):
                    if pending.get("from_agent") == agent_name and pending.get("tool_result"):
                        is_resuming = True
                        tool_interactions = memory.get("tool_interactions", [])
                        pending_tool_result = (pending["tool_name"], pending["tool_result"])
                        logger.info(f"{agent_name} resuming after tool '{pending['tool_name']}'")

                # 2. 检查subagent恢复
                elif pending := state.get("subagent_pending"):
                    if agent_name == "lead_agent" and pending.get("subagent_result"):
                        is_resuming = True
                        tool_interactions = memory.get("tool_interactions", [])
                        pending_tool_result = ("call_subagent", pending["subagent_result"])
                        logger.info(f"{agent_name} resuming after subagent")

                # ========== 构建messages ==========
                messages = await ContextManager.build_agent_messages(
                    agent=agent,
                    state=state,
                    instruction=instruction,
                    tool_interactions=tool_interactions,
                    pending_tool_result=pending_tool_result,
                    artifact_manager=self.artifact_manager
                )

                # 如果达到工具轮数上限，添加系统消息提醒总结
                if hit_tool_limit:
                    messages.append({
                        "role": "system",
                        "content": "⚠️ You have reached the maximum number of tool calls. Please summarize your findings and provide a final response."
                    })

                # ========== 流式执行Agent ==========
                final_response = None
                llm_start_time = datetime.now()

                async for event in agent.stream(
                    messages=messages,
                    is_resuming=is_resuming
                ):
                    # 通过 StreamWriter 发送自定义事件
                    writer({
                        "type": event.type.value,
                        "agent": event.agent,
                        "timestamp": event.timestamp.isoformat(),
                        "data": self._serialize_event_data(event.data)
                    })

                    # 保存最终响应
                    if event.data:
                        final_response = event.data

                llm_end_time = datetime.now()
                llm_duration_ms = int((llm_end_time - llm_start_time).total_seconds() * 1000)

                # ========== 更新状态 ==========
                if final_response:
                    merge_agent_response_to_state(
                        state,
                        agent_name,
                        final_response,
                        is_resuming=is_resuming
                    )

                    # ========== 更新 execution_metrics ==========
                    token_usage = final_response.token_usage or {}
                    append_agent_execution(
                        metrics=state["execution_metrics"],
                        agent_name=agent_name,
                        model=agent.config.model,
                        token_usage={
                            "input_tokens": token_usage.get("input_tokens", 0),
                            "output_tokens": token_usage.get("output_tokens", 0),
                            "total_tokens": token_usage.get("input_tokens", 0) + token_usage.get("output_tokens", 0)
                        },
                        started_at=llm_start_time.isoformat(),
                        completed_at=llm_end_time.isoformat(),
                        llm_duration_ms=llm_duration_ms
                    )
                else:
                    # 如果没有响应，创建错误响应
                    error_response = AgentResponse(
                        success=False,
                        content=f"{agent_name} failed to produce response"
                    )
                    merge_agent_response_to_state(state, agent_name, error_response)
                    state["phase"] = ExecutionPhase.COMPLETED

            except Exception as e:
                logger.exception(f"Error in {agent_name}: {e}")

                error_response = AgentResponse(
                    success=False,
                    content=f"Error in {agent_name}: {str(e)}",
                    metadata={'error': str(e)}
                )

                merge_agent_response_to_state(state, agent_name, error_response)
                state["phase"] = ExecutionPhase.COMPLETED

            return state
        
        return agent_node
    
    def _serialize_event_data(self, data: Any) -> Dict:
        """
        序列化事件数据为可JSON序列化的字典
        
        Args:
            data: AgentResponse 或其他数据
            
        Returns:
            可序列化的字典
        """
        if data is None:
            return None
        
        if isinstance(data, AgentResponse):
            return {
                "success": data.success,
                "content": data.content,
                "tool_calls": data.tool_calls,
                "reasoning_content": data.reasoning_content,
                "metadata": data.metadata,
                "routing": data.routing,
                "token_usage": data.token_usage
            }
        
        # 其他数据类型直接返回
        return data
    
    def _add_routing_rules(self, agent_name: str) -> None:
        """
        为Agent添加路由规则
        
        Args:
            agent_name: Agent名称
        """
        def route_func(state: AgentState) -> str:
            """
            基于phase的路由逻辑

            路由规则：
            1. TOOL_EXECUTING → tool_execution
            2. SUBAGENT_EXECUTING → 目标subagent
            3. LEAD_EXECUTING → lead_agent（subagent完成后返回）
            4. COMPLETED → END
            """
            phase = state["phase"]
            current_agent = state.get("current_agent")

            # 1. 工具执行
            if phase == ExecutionPhase.TOOL_EXECUTING:
                return "tool_execution"

            # 2. Subagent执行
            elif phase == ExecutionPhase.SUBAGENT_EXECUTING:
                target = state["subagent_pending"]["target"]
                return target

            # 3. Lead执行（subagent完成后返回）
            elif phase == ExecutionPhase.LEAD_EXECUTING:
                return "lead_agent"

            # 4. 完成
            elif phase == ExecutionPhase.COMPLETED:
                return END

            else:
                logger.error(f"Unexpected routing in phase: {phase}")
                return END

        # 构建路由映射（包含所有可能的目标）
        route_map = {
            "tool_execution": "tool_execution",
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
    
    async def compile(
        self,
        checkpointer: Optional[Any] = None,
        interrupt_before: Optional[list] = None,
        db_path: str = "data/langgraph.db"
    ) -> Any:
        """
        编译Graph

        Args:
            checkpointer: LangGraph checkpointer 实例（用于状态持久化）
                如果为 None，使用 AsyncSqliteSaver 进行持久化
            interrupt_before: 中断前节点列表
            db_path: SQLite 数据库文件路径（仅当 checkpointer 为 None 时使用）

        Returns:
            编译后的Graph
        """

        # 1. 为 tool_execution 添加出边
        def route_after_tool_execution(state: AgentState) -> str:
            """从工具执行返回原agent"""
            phase = state["phase"]

            if phase == ExecutionPhase.LEAD_EXECUTING:
                return "lead_agent"
            elif phase == ExecutionPhase.SUBAGENT_EXECUTING:
                # 读取from_agent，返回原agent
                pending = state.get("pending_tool_call")
                if pending:
                    return pending["from_agent"]
                return "lead_agent"
            else:
                return END

        # 构建route_map（包含所有agents）
        route_map = {"lead_agent": "lead_agent", END: END}
        for agent_name in self.agents.keys():
            route_map[agent_name] = agent_name

        # 添加条件边
        self.workflow.add_conditional_edges(
            "tool_execution",
            route_after_tool_execution,
            route_map
        )

        # 2. 编译（调用时使用 stream_mode="custom"）
        if checkpointer is None:
            # 使用 AsyncSqliteSaver 进行持久化
            checkpointer = await create_async_sqlite_checkpointer(db_path)

        if interrupt_before is None:
            interrupt_before = []

        compiled = self.workflow.compile(
            checkpointer=checkpointer,
            interrupt_before=interrupt_before
        )

        logger.info(f"Graph compiled with {len(self.agents)} agents (using AsyncSqliteSaver)")
        return compiled


async def create_multi_agent_graph(
    tool_permissions: Optional[Dict[str, "ToolPermission"]] = None,
    artifact_manager: Optional["ArtifactManager"] = None,
    checkpointer: Optional[Any] = None,
    db_path: str = "data/langgraph.db"
):
    """
    创建多Agent Graph的工厂函数

    Args:
        tool_permissions: 工具权限配置字典
            格式: {"tool_name": ToolPermission.LEVEL}
            例如: {"send_email": ToolPermission.CONFIRM}
        artifact_manager: ArtifactManager 实例（用于持久化）
            如果为 None，artifact 工具将不可用
        checkpointer: LangGraph checkpointer 实例（用于状态持久化）
            如果为 None，使用 AsyncSqliteSaver 进行持久化
        db_path: SQLite 数据库文件路径（仅当 checkpointer 为 None 时使用）

    Returns:
        编译后的Graph
    """
    from agents.lead_agent import create_lead_agent
    from agents.search_agent import create_search_agent
    from agents.crawl_agent import create_crawl_agent
    from tools.registry import ToolRegistry
    from tools.implementations.artifact_ops import create_artifact_tools
    from tools.implementations.call_subagent import CallSubagentTool
    from tools.implementations.web_search import WebSearchTool
    from tools.implementations.web_fetch import WebFetchTool

    # 创建Graph构建器（传入 artifact_manager 用于上下文注入）
    graph_builder = ExtendableGraph(artifact_manager=artifact_manager)

    # 创建工具注册中心
    registry = ToolRegistry()

    # 创建工具列表
    tools = [
        CallSubagentTool(),
        WebSearchTool(),
        WebFetchTool(),
    ]

    # Artifact 工具（需要 manager）
    if artifact_manager:
        tools.extend(create_artifact_tools(artifact_manager))

    # 应用权限配置
    if tool_permissions:
        for tool in tools:
            if tool.name in tool_permissions:
                tool.permission = tool_permissions[tool.name]

    # 注册所有工具
    for tool in tools:
        registry.register_tool_to_library(tool)

    # 创建Agent（不带toolkit，后续根据config.required_tools创建并绑定）
    lead = create_lead_agent()
    search = create_search_agent()
    crawl = create_crawl_agent()

    # 为每个Agent创建toolkit并绑定（从config.required_tools读取）
    for agent in [lead, search, crawl]:
        if agent.config.required_tools:
            toolkit = registry.create_agent_toolkit(
                agent.config.name,
                tool_names=agent.config.required_tools
            )
            agent.toolkit = toolkit

    # 注册子Agent到Lead（从config读取元信息）
    lead.register_subagent(search.config)
    lead.register_subagent(crawl.config)
    
    # 注册到Graph（顺序重要：先注册subagent）
    graph_builder.register_agent(search)
    graph_builder.register_agent(crawl)
    graph_builder.register_agent(lead)
    
    # 设置入口点
    graph_builder.set_entry_point("lead_agent")

    # 编译（传入 checkpointer）
    return await graph_builder.compile(checkpointer=checkpointer, db_path=db_path)


async def create_async_sqlite_checkpointer(db_path: str = "data/langgraph.db"):
    """
    创建 AsyncSqliteSaver checkpointer

    用于 LangGraph 状态持久化，支持中断恢复和对话历史。

    Args:
        db_path: SQLite 数据库文件路径

    Returns:
        AsyncSqliteSaver 实例

    使用示例:
        ```python
        from core.graph import create_multi_agent_graph, create_async_sqlite_checkpointer

        # 创建 checkpointer
        checkpointer = await create_async_sqlite_checkpointer("data/langgraph.db")

        # 创建 graph
        graph = await create_multi_agent_graph(checkpointer=checkpointer)
        ```

    注意：
        - 需要安装 langgraph-checkpoint-sqlite 和 aiosqlite 包
        - 数据库文件会自动创建
        - 建议与应用数据库使用不同的文件（LangGraph 有自己的 schema）
        - 调用方负责在程序结束时关闭连接（可选）
    """
    import os
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # 确保目录存在
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    # 创建 aiosqlite 连接
    conn = await aiosqlite.connect(db_path)

    # 创建 AsyncSqliteSaver
    checkpointer = AsyncSqliteSaver(conn)

    # 初始化表结构
    await checkpointer.setup()

    logger.info(f"Created AsyncSqliteSaver checkpointer: {db_path}")
    return checkpointer