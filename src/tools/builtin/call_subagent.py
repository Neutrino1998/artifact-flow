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
    2. Engine 检测到后调用 execute() 验证参数（agent_name、instruction）
    3. 验证通过 → Engine 原地递归 await 子 agent 的循环（嵌套串行），
       子 agent 最终回复包成 <subagent_result> 作为本调用的 tool_result
    4. 验证失败 → 当作普通 tool_call，返回错误让 Lead 修正
    5. 同轮可与其他工具/多个 call_subagent 混排，按自然序串行执行
    """

    def __init__(self, valid_agents: Optional[List[str]] = None):
        super().__init__(
            name="call_subagent",
            description="Call a specialized sub-agent to handle specific tasks.",
            permission=ToolPermission.AUTO
        )
        self._valid_agents = valid_agents
    
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
            ),
            ToolParameter(
                name="fresh_start",
                type="boolean",
                description=(
                    "Whether the sub-agent starts with a fresh conversation context "
                    "(True, default) or continues from its prior calls within this conversation "
                    "(False). Use False only when you need the sub-agent to build on earlier "
                    "exchanges it had in this session."
                ),
                required=False
            )
        ]

    async def execute(self, **params) -> ToolResult:
        """
        纯参数验证，无副作用。

        Engine 在检测到 call_subagent 时先调用此方法验证；验证通过后由
        _execute_tools 原地递归 await 目标 agent 的 _run_agent 循环，
        返回值包成 <subagent_result> 作为本调用的 tool_result。
        """
        agent_name = params.get("agent_name")
        instruction = params.get("instruction", "").strip()

        # 基本验证
        if self._valid_agents is not None and agent_name not in self._valid_agents:
            return ToolResult(
                success=False,
                error=f"Invalid agent_name '{agent_name}'. Must be one of: {', '.join(self._valid_agents)}"
            )

        if not instruction:
            return ToolResult(
                success=False,
                error="instruction parameter cannot be empty"
            )

        # 始终打原始长度,便于 operator 区分"完整 100 字内指令"与"被切掉的长指令"。
        _instr_len = len(instruction)
        _instr_preview = instruction[:100]
        _truncated = "" if _instr_len <= 100 else f" (truncated, {_instr_len} chars total)"
        logger.info(f"Routing to {agent_name}: {_instr_preview!r}{_truncated}")

        return ToolResult(success=True)

    @staticmethod
    def parse_fresh_start(params: Dict[str, Any]) -> bool:
        """
        Parse the `fresh_start` parameter from XML-sourced params (string-typed) into bool.
        Default True.
        """
        raw = params.get("fresh_start")
        if raw is None:
            return True
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() not in ("false", "0", "no", "off")

