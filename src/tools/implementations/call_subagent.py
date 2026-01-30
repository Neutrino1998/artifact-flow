"""
Subagent 调用工具
用于 Lead Agent 路由到 SubAgent，执行时验证参数有效性
"""

from typing import List, Dict, Any, Optional
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class CallSubagentTool(BaseTool):
    """
    Subagent 调用工具

    工作原理：
    1. Lead Agent 通过 XML 格式调用此工具
    2. base.py 检测到后调用 execute() 验证参数（agent_name、instruction）
    3. 验证通过 → 设置 subagent 路由，Graph 转发到目标 SubAgent
    4. 验证失败 → 当作普通 tool_call，返回错误让 Lead 修正
    5. SubAgent 完成后，结果通过 AgentState 回传给 Lead Agent
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
        验证参数并返回路由信息。

        Agent 在 base.py 中检测到 call_subagent 时会调用此方法进行验证，
        验证通过后再设置 response.routing 进行路由。
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

