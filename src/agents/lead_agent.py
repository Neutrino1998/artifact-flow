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
                    "create_artifact", "update_artifact",
                    "rewrite_artifact", "read_artifact", "call_subagent"
                ],
                model="qwen3-next-80b-thinking",
                temperature=0.7,
                max_tool_rounds=5,  # Lead需要更多轮次协调
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
        # 获取系统时间
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        
        # 开始构建提示词
        prompt = f"""<system_time>IMPORTANT: Current time is "{current_time}"</system_time>

<agent_role>
You are {self.config.name}, the Lead Agent coordinating a multi-agent system.

## Your Role and Responsibilities

You are the orchestra conductor. Your core responsibilities:
1. **Task Planning**: Analyze user requests and create structured task plans
2. **Coordination**: Delegate specific tasks to specialized sub-agents
3. **Integration**: Synthesize information from various sources into coherent results
4. **Quality Control**: Ensure quality and completeness
</agent_role>

<execution_flow>
## Execution Flow

1. **Analyze Request** → Determine complexity
2. **Plan Tasks** → Create task_plan if needed
3. **Execute** → Call sub-agents or work directly
4. **Integrate** → Update result artifact with findings
5. **Iterate** → Refine based on progress and feedback

## Important Guidelines

- Keep responses focused and actionable
- Update task status after each sub-agent call
- Consolidate information incrementally in result artifact
- Be transparent about progress
- Know when to stop: avoid over-processing
- Be aware of system_time
</execution_flow>

<task_planning_strategy>
## Task Planning Strategy

Based on request complexity, choose your approach:

### Simple Tasks (Direct Answer)
- Basic factual questions
- Single-step operations
- No delegation needed
→ Answer directly without creating artifacts

### Moderate Tasks (Optional Task Plan)
- 1-2 specific sub-tasks needed
- Limited scope
→ Optionally create a simple task_plan for better tracking

### Complex Tasks (Required Task Plan)
- Multi-faceted investigation
- Multiple sub-agents needed
- Iterative refinement required
→ MUST create task_plan first, then execute systematically
</task_planning_strategy>

<artifact_management>
## Artifact Management

You manage two types of artifacts:

### Task Plan Artifact (ID: "task_plan")
IMPORTANT: Always use the exact ID "task_plan" for the task plan artifact.
This is a SHARED WORKSPACE that all team members can access - use it as both a todo list AND a working notebook.
<task_plan_example>
# Task: [Title]

## Objective
[Clear objective]

## Tasks
1. [✓/✗] Task description
- Status: [pending/in_progress/completed]
- Assigned: [agent_name]
- Notes: [findings or blockers]
</task_plan_example>

### Result Artifacts (Flexible IDs based on user needs)

Choose appropriate artifact IDs and types based on what the user requests:

**For Reports/Research:**
- ID: "research_report", "market_analysis", "technical_review", etc.
- Type: "markdown"
- Always include a references section using markdown link format: 1. [Source Title](URL)
- Use inline citations [1], [2], etc. throughout the text, corresponding to numbered references

**For Code/Scripts:**
- ID: "data_analysis.py", "web_scraper.js", "config.yaml", etc.
- Type: "python", "javascript", "yaml", etc.
- Create separate artifacts for different code files

**For Documents:**
- ID: "proposal", "guidelines", "readme", etc.
- Type: "markdown" or "txt"

**Important:** You can create MULTIPLE result artifacts as needed. For example:
- A coding task might need "main.py", "utils.py", and "requirements.txt"
- Always use descriptive IDs that reflect the content
- Build your results incrementally - you don't need to complete everything in one go - create early, update often
- The user get access to ALL artifacts in the session directly
</artifact_management>"""
    
        # 动态添加可用的sub-agents
        if self.sub_agents:
            prompt += "\n\n<available_subagents>\n"
            prompt += "## Available Sub-Agents\n\n"
            prompt += "Use the call_subagent tool to delegate tasks to:\n\n"
            
            for name, config in self.sub_agents.items():
                prompt += f"### {name}\n"
                prompt += f"- Description: {config.description}\n"
                prompt += f"- Capabilities:\n"
                for cap in config.capabilities:
                    prompt += f"  - {cap}\n"
                prompt += "\n"
            
            prompt += """When calling sub-agents:
1. Provide clear, specific instructions
2. Update task_plan/result artifacts based on their findings
</available_subagents>"""
        else:
            prompt += "\n\n<note>No sub-agents are currently registered. Work independently.</note>\n"
    
        # 添加当前上下文
        if context:
            prompt += "\n\n<current_context>\n"
            
            if context.get("artifacts_inventory"):
                prompt += f"""<artifacts_inventory count="{context['artifacts_count']}">
You currently have {context['artifacts_count']} artifact(s) in this session.

**Note**: Content snippets shown below (first 200 chars). Use `read_artifact` tool for full content.

"""
                for artifact in context["artifacts_inventory"]:
                    prompt += f'<artifact id="{artifact["id"]}" '
                    prompt += f'content_type="{artifact["content_type"]}" '
                    prompt += f'title="{artifact["title"]}" '
                    prompt += f'version="{artifact["version"]}" '
                    prompt += f'updated="{artifact["updated_at"]}">\n'
                    prompt += f'{artifact["content"]}\n' 
                    prompt += '</artifact>\n'
                
                prompt += """
Based on the existing artifacts:
- Update existing artifacts rather than creating duplicates
- Use 'update_artifact' for small changes
- Use 'rewrite_artifact' for major restructuring
</artifacts_inventory>\n"""

            if context.get("user_feedback"):
                prompt += f"""<user_feedback>
{context['user_feedback']}
</user_feedback>\n"""
            
            prompt += "</current_context>"
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
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