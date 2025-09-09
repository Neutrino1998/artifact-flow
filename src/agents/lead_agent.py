"""
Lead Agent实现
负责任务协调、信息整合、用户交互
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from agents.base import BaseAgent, AgentConfig, AgentResponse
from utils.logger import get_logger

logger = get_logger("Agents")


class SubAgent:
    """子Agent注册信息"""
    def __init__(self, name: str, description: str, capabilities: List[str]):
        self.name = name
        self.description = description
        self.capabilities = capabilities


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
                model="qwen-plus",
                temperature=0.7,
                max_tool_rounds=5,  # Lead需要更多轮次协调
                streaming=True
            )
        
        super().__init__(config, toolkit)
        
        # Lead特有的状态
        self.current_task_plan_id = None
        self.current_result_id = None
        
        # 注册的子Agent列表
        self.sub_agents: Dict[str, SubAgent] = {}
    
    def register_subagent(self, agent: SubAgent):
        """
        注册子Agent
        
        Args:
            agent: SubAgent实例
        """
        self.sub_agents[agent.name] = agent
        logger.info(f"Registered sub-agent: {agent.name}")
    
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
        prompt = f"""<system_time>{current_time}</system_time>

<agent_role>
You are {self.config.name}, the Lead Agent coordinating a multi-agent system.

## Your Role and Responsibilities

You are the orchestra conductor. Your core responsibilities:
1. **Task Planning**: Analyze user requests and create structured task plans
2. **Coordination**: Delegate specific tasks to specialized sub-agents
3. **Integration**: Synthesize information from various sources into coherent results
4. **Quality Control**: Ensure quality and completeness
</agent_role>

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
→ Optionally create task_plan for better tracking

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
⚠️ IMPORTANT: Always use the exact ID "task_plan" for the task plan artifact.
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

## Progress Summary
- Overall: [X%]
- Last Updated: [timestamp]
</task_plan_example>

### Result Artifacts (Flexible IDs based on user needs)

Choose appropriate artifact IDs and types based on what the user requests:

**For Reports/Research:**
- ID: "research_report", "market_analysis", "technical_review", etc.
- Type: "markdown"
- Example structure:
<result_example>
# [Topic] Research Report

## Executive Summary
[Key findings overview]

## Detailed Analysis
[Structured findings]

## Conclusions
[Key takeaways]

## References
[Sources and citations]
</result_example>

**For Code/Scripts:**
- ID: "data_analysis.py", "web_scraper.js", "config.yaml", etc.
- Type: "python", "javascript", "yaml", etc.
- Create separate artifacts for different code files

**For Documents:**
- ID: "proposal", "guidelines", "readme", etc.
- Type: "markdown" or "txt"

**Important:** You can create MULTIPLE result artifacts as needed. For example:
- A research task might need both "research_report" and "data_summary"
- A coding task might need "main.py", "utils.py", and "requirements.txt"
- Always use descriptive IDs that reflect the content
</artifact_management>"""
    
        # 动态添加可用的sub-agents
        if self.sub_agents:
            prompt += "\n\n<available_subagents>\n"
            prompt += "## Available Sub-Agents\n\n"
            prompt += "Use the call_subagent tool to delegate tasks to:\n\n"
            
            for name, agent in self.sub_agents.items():
                prompt += f"### {name}\n"
                prompt += f"- Description: {agent.description}\n"
                prompt += f"- Capabilities:\n"
                for cap in agent.capabilities:
                    prompt += f"  - {cap}\n"
                prompt += "\n"
            
            prompt += """When calling sub-agents:
1. Provide clear, specific instructions
2. Include relevant context from task_plan
3. Wait for their results before proceeding
4. Update task_plan based on their findings
</available_subagents>"""
        else:
            prompt += "\n\n<note>No sub-agents are currently registered. Work independently.</note>\n"
        
        prompt += """

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
</execution_flow>"""
    
        # 添加当前上下文
        if context:
            prompt += "\n\n<current_context>\n"
            
            if context.get("task_plan_content"): 
                prompt += f"""<task_plan version="{context.get('task_plan_version', 1)}" updated="{context.get('task_plan_updated', 'unknown')}">
{context['task_plan_content']}
</task_plan>\n"""
        
            # 显示当前artifacts状态
            if context.get("artifacts_inventory"):
                prompt += f"""<artifacts_status count="{context['artifacts_count']}">
You currently have {context['artifacts_count']} artifact(s) in this session:
"""
                for artifact in context["artifacts_inventory"]:
                    status_icon = "📝" if artifact["content_type"] == "markdown" else "📄"
                    prompt += f"\n{status_icon} **{artifact['id']}** (v{artifact['version']})"
                    prompt += f"\n   - Type: {artifact['content_type']}"
                    prompt += f"\n   - Title: {artifact['title']}"
                    prompt += f"\n   - Last updated: {artifact['updated_at']}"
                
                prompt += """

Based on the existing artifacts:
- Update existing artifacts rather than creating duplicates
- Use 'update_artifact' for small changes
- Use 'rewrite_artifact' for major restructuring
</artifacts_status>\n"""

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
    
    async def create_task_plan(
        self,
        task_description: str,
        requirements: Optional[List[str]] = None
    ) -> AgentResponse:
        """
        创建任务计划
        
        Args:
            task_description: 任务描述
            requirements: 具体要求列表
            
        Returns:
            包含task_plan的响应
        """
        instruction = f"Create a comprehensive task plan for: {task_description}"
        
        if requirements:
            instruction += "\n\nSpecific Requirements:\n"
            for req in requirements:
                instruction += f"- {req}\n"
        
        instruction += "\nPlease create a task_plan artifact with clear objectives and task breakdown."
        
        return await self.execute(instruction)
    
    async def handle_user_feedback(
        self,
        feedback: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentResponse:
        """
        处理用户反馈并调整任务方向
        
        Args:
            feedback: 用户反馈内容
            context: 当前上下文（包含task_plan和result）
            
        Returns:
            更新后的响应
        """
        enhanced_context = context or {}
        enhanced_context["user_feedback"] = feedback
        
        instruction = f"""Based on the user feedback, please:
1. Review and adjust the task plan if needed
2. Identify what additional work is required
3. Execute the necessary updates
4. Provide a summary of changes made

User Feedback: {feedback}"""
        
        return await self.execute(instruction, enhanced_context)
    
    def extract_routing_decision(self, tool_calls: List[Dict]) -> Optional[str]:
        """
        从工具调用中提取路由决策
        
        Args:
            tool_calls: 工具调用历史
            
        Returns:
            需要路由到的agent名称，None表示不需要路由
        """
        for call in tool_calls:
            if call.get("tool") == "call_subagent":
                result = call.get("result", {})
                if result.get("success") and result.get("data"):
                    # 检查是否是路由指令
                    data = result["data"]
                    if data.get("_is_routing_instruction"):
                        return data.get("_route_to")
        return None


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