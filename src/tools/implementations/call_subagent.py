"""
Subagent调用工具（伪装路由工具）
这是一个特殊的工具，实际不执行操作，而是生成LangGraph路由指令
"""

from typing import List, Dict, Any, Optional
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class CallSubagentTool(BaseTool):
    """
    Subagent调用工具（伪装工具）
    
    这个工具实际上不执行任何操作，而是返回路由指令，
    供LangGraph的条件路由识别并转发到相应的Subagent节点。
    
    工作原理：
    1. Lead Agent通过XML格式"调用"这个工具
    2. 工具解析参数并返回特殊的路由标记
    3. Graph的条件路由识别标记，路由到对应Subagent
    4. Subagent处理完成后，结果通过AgentState回传给Lead Agent
    """
    
    def __init__(self):
        super().__init__(
            name="call_subagent",
            description="Call a specialized sub-agent to handle specific tasks",
            permission=ToolPermission.AUTO
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="agent_name",
                type="string", 
                description="Sub-agent type: check available_subagents section for available agents",
                required=True
            ),
            ToolParameter(
                name="instruction",
                type="string",
                description="Specific task instruction for the sub-agent. Be concise about what you need.",
                required=True
            )
        ]
    
    async def execute(self, **params) -> ToolResult:
        """
        注意：此方法通常不会被调用。

        Agent 在 base.py 中检测到 call_subagent 时，会直接从 tool_call.params
        提取路由信息并设置 response.routing，不经过工具执行。

        保留此实现是为了：
        1. 参数验证（虽然目前在 base.py 中处理）
        2. 架构变化时的兼容性
        """
        agent_name = params.get("agent_name")
        instruction = params.get("instruction", "").strip()

        # 基本验证
        valid_agents = ["search_agent", "crawl_agent"]
        if agent_name not in valid_agents:
            return ToolResult(
                success=False,
                error=f"Invalid agent_name '{agent_name}'. Must be one of: {', '.join(valid_agents)}"
            )

        if not instruction:
            return ToolResult(
                success=False,
                error="instruction parameter cannot be empty"
            )

        logger.info(f"Routing to {agent_name}: {instruction[:100]}...")

        return ToolResult(
            success=True,
            data={
                "agent_name": agent_name,
                "instruction": instruction
            }
        )

