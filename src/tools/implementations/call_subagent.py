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
            permission=ToolPermission.PUBLIC
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
        "执行"工具调用（实际是生成路由指令）
        
        Args:
            agent_name: 目标Agent类型
            instruction: 任务指令
            
        Returns:
            包含路由信息的ToolResult
        """
        # 验证agent_name
        valid_agents = ["search_agent", "crawl_agent"]
        agent_name = params.get("agent_name")
        
        if agent_name not in valid_agents:
            return ToolResult(
                success=False,
                error=f"Invalid agent_name '{agent_name}'. Must be one of: {', '.join(valid_agents)}"
            )
        
        instruction = params.get("instruction", "").strip()
        if not instruction:
            return ToolResult(
                success=False,
                error="instruction parameter cannot be empty"
            )
        
        # 记录路由请求
        logger.info(f"Routing request: {agent_name} - {instruction[:100]}...")
        
        # 🎭 返回特殊的路由指令（不是真正的工具执行结果）
        return ToolResult(
            success=True,
            data={
                # 🚦 路由控制信息
                "_route_to": agent_name,
                "_is_routing_instruction": True,  # 特殊标记
                
                # 📋 任务信息
                "instruction": instruction,
                
                # 📊 元数据
                "requested_at": self._get_timestamp(),
                "requested_by": "lead_agent"
            },
            metadata={
                "tool_type": "routing",
                "target_agent": agent_name,
                "instruction_length": len(instruction)
            }
        )
    
    def _get_timestamp(self) -> str:
        """获取当前时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()

