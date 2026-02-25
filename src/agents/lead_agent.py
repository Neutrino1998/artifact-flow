"""
Lead Agent实现
负责任务协调、信息整合、用户交互
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from agents.base import BaseAgent, AgentConfig, AgentResponse
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class LeadAgent(BaseAgent):
    """
    Lead Agent - 任务协调者
    
    核心职责：
    1. 任务规划：根据复杂度创建task_plan
    2. 协调执行：调用sub agents完成具体任务
    3. 信息整合：将结果整合到result artifact
    4. 用户交互：响应用户反馈，迭代优化
    
    工具配置：
    - Artifact操作工具（create/update/rewrite/read_artifact）
    - CallSubagentTool（路由到sub agents）
    """
    
    def __init__(self, config: Optional[AgentConfig] = None, toolkit=None):
        """
        初始化Lead Agent

        Args:
            config: Agent配置
            toolkit: 工具包（应包含artifact工具和call_subagent工具）
        """
        if not config:
            config = AgentConfig(
                name="lead_agent",
                description="Task coordinator and information integrator",
                required_tools=[
                    "create_artifact", 
                    "update_artifact",
                    "rewrite_artifact", 
                    "read_artifact", 
                    "call_subagent"
                ],
                model="qwen3.5-plus",
                temperature=0.7,
                max_tool_rounds=100,  # Lead需要更多轮次协调
                streaming=True
            )

        super().__init__(config, toolkit)

        # 注册的子Agent配置（用于生成system prompt）
        self.sub_agents: Dict[str, AgentConfig] = {}

    def register_subagent(self, config: AgentConfig):
        """
        注册子Agent

        Args:
            config: 子Agent的配置
        """
        self.sub_agents[config.name] = config
        logger.info(f"Registered sub-agent: {config.name}")
    
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建Lead Agent的系统提示词

        Args:
            context: 包含task_plan等上下文信息

        Returns:
            系统提示词
        """
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")

        sections = [
            self._build_system_time(current_time),
            self._build_role_section(),
            self._build_task_plan_section(),
            self._build_artifacts_section(),
            self._build_subagents_section(),
            self._build_context_section(context),
        ]

        return "\n\n".join(s for s in sections if s)

    def _build_system_time(self, current_time: str) -> str:
        return f'<system_time>Current time: {current_time}</system_time>'

    def _build_role_section(self) -> str:
        return f"""<role>
You are {self.config.name}, the Lead Agent coordinating a multi-agent system.

**Execution Flow:**
1. **Analyze Request** — Determine complexity
2. **Plan Tasks** — Create task_plan if needed
3. **Execute** — Call sub-agents or work directly
4. **Integrate** — Update result artifact with findings
5. **Iterate** — Refine based on progress and feedback

**Guidelines:**
- Keep responses focused and actionable
- Know when to stop — avoid over-processing
</role>"""

    def _build_task_plan_section(self) -> str:
        return """<task_plan>
For tasks requiring multiple steps or sub-agent calls, create a task_plan artifact (ID: `task_plan`).

This is a shared workspace — use it as both a todo list and a working notebook. Conversation history may be compacted over long sessions, so note down important details and findings here.

<task_plan_example>
# Task: [Title]

## Tasks
1. [✓/✗] Task description — agent_name — [findings or blockers]
2. [✓/✗] Task description — agent_name — [findings or blockers]
</task_plan_example>
</task_plan>"""

    def _build_artifacts_section(self) -> str:
        return """<artifacts>
You can create MULTIPLE result artifacts. Use descriptive IDs that reflect the content.

- **Reports/Research** (`text/markdown`): "research_report", "market_analysis", etc. Include a references section with `[Source Title](URL)` and inline citations `[1]`, `[2]`.
- **Code/Scripts** (`text/x-python`, `text/javascript`, etc.): "data_analysis.py", "web_scraper.js", etc. Create separate artifacts for different files.
- **Documents** (`text/markdown` or `text/plain`): "proposal", "guidelines", "readme", etc.
</artifacts>"""

    def _build_subagents_section(self) -> str:
        if not self.sub_agents:
            return "<note>No sub-agents are currently registered. Work independently.</note>"

        lines = ["<available_subagents>"]
        lines.append("Use the `call_subagent` tool to delegate tasks. Provide clear, specific instructions.\n")

        for name, config in self.sub_agents.items():
            lines.append(f"**{name}**: {config.description}")
            for cap in config.capabilities:
                lines.append(f"  - {cap}")
            lines.append("")

        lines.append("</available_subagents>")
        return "\n".join(lines)

    def _build_context_section(self, context: Optional[Dict[str, Any]]) -> Optional[str]:
        if not context:
            return None

        parts: List[str] = []

        if context.get("artifacts_inventory"):
            count = context["artifacts_count"]
            inv = f'{count} artifact(s) in this session.\n'
            inv += '<artifacts_inventory>\n'
            for artifact in context["artifacts_inventory"]:
                source = artifact.get("source", "agent")
                inv += f'<artifact id="{artifact["id"]}" type="{artifact["content_type"]}" title="{artifact["title"]}" version="{artifact["version"]}" source="{source}" updated="{artifact["updated_at"]}">\n'
                inv += f'{artifact["content"]}\n'
                inv += '</artifact>\n'
            # Keep user_upload note (context-specific, needed for uploaded docs)
            inv += '\nArtifacts with source: user_upload are documents uploaded by the user — use `read_artifact` for full content if relevant.\n'
            inv += '</artifacts_inventory>'
            parts.append(inv)

        if context.get("user_feedback"):
            parts.append(f"<user_feedback>\n{context['user_feedback']}\n</user_feedback>")

        if not parts:
            return None

        return "<current_context>\n" + "\n".join(parts) + "\n</current_context>"
    
    def format_final_response(self, content: str) -> str:
        """
        格式化Lead Agent的最终响应

        Lead Agent的响应就是其原始内容，不需要额外格式化
        """
        return content

# 工厂函数
def create_lead_agent(toolkit=None) -> LeadAgent:
    """
    创建Lead Agent实例
    
    Args:
        toolkit: 工具包
        
    Returns:
        配置好的Lead Agent实例
    """
    return LeadAgent(toolkit=toolkit)
