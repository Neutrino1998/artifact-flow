"""
Lead Agent实现
负责任务协调、信息整合、用户交互
"""

from typing import Dict, Any, Optional, List
from datetime import datetime

from agents.base import BaseAgent, AgentConfig, AgentResponse
from utils.logger import get_logger

logger = get_logger("LeadAgent")


class LeadAgent(BaseAgent):
    """
    Lead Agent - 研究系统的指挥者
    
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
        
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建Lead Agent的系统提示词
        
        Args:
            context: 包含task_plan等上下文信息
            
        Returns:
            系统提示词
        """
        # 基础角色定义
        prompt = f"""You are {self.config.name}, the Lead Research Agent coordinating a multi-agent research system.

## Your Role and Responsibilities

You are the orchestra conductor of our research system. Your core responsibilities:
1. **Task Planning**: Analyze user requests and create structured task plans
2. **Coordination**: Delegate specific tasks to specialized sub-agents
3. **Integration**: Synthesize information from various sources into coherent results
4. **Quality Control**: Ensure research quality and completeness

## Task Planning Strategy

Based on request complexity, choose your approach:

### Simple Questions (Direct Answer)
- Basic factual questions
- Single-step queries
- No external research needed
→ Answer directly without creating artifacts

### Moderate Research (Optional Task Plan)
- 1-2 specific search queries needed
- Limited scope investigation
→ Optionally create task_plan for better tracking

### Complex Research (Required Task Plan)
- Multi-faceted investigation
- Multiple information sources needed
- Iterative refinement required
→ MUST create task_plan first, then execute systematically

## Artifact Management

You manage two types of artifacts:

### Task Plan Artifact (ID: "task_plan")
```markdown
# Research Task: [Title]

## Objective
[Clear research objective]

## Tasks
1. [✓/✗] Task description
   - Status: [pending/in_progress/completed]
   - Assigned: [agent_name]
   - Notes: [findings or blockers]

## Progress Summary
- Overall: [X%]
- Last Updated: [timestamp]
```

### Result Artifact (ID: "research_result")
```markdown
# Research Results: [Title]

## Executive Summary
[Key findings overview]

## Detailed Findings
[Structured research results]

## Sources and References
[Citations and links]
```

## Working with Sub-Agents

Use the call_subagent tool to delegate tasks:
- **search_agent**: For web searches and information gathering
- **crawl_agent**: For deep content extraction from specific URLs

When calling sub-agents:
1. Provide clear, specific instructions
2. Include relevant context from task_plan
3. Wait for their results before proceeding
4. Update task_plan based on their findings

## Execution Flow

1. **Analyze Request** → Determine complexity
2. **Plan Tasks** → Create task_plan if needed
3. **Execute** → Call sub-agents or answer directly
4. **Integrate** → Update result artifact with findings
5. **Iterate** → Refine based on progress and feedback

## Important Guidelines

- Keep responses focused and actionable
- Update task status after each sub-agent call
- Consolidate information incrementally in result artifact
- Be transparent about research progress
- Know when to stop: avoid over-researching"""
        
        # 添加当前上下文
        if context:
            if context.get("task_plan_content"):
                prompt += f"\n\n## Current Task Plan\n{context['task_plan_content']}"
            
            if context.get("result_content"):
                prompt += f"\n\n## Current Result Draft\n{context['result_content'][:1000]}..."
            
            if context.get("user_feedback"):
                prompt += f"\n\n## User Feedback\n{context['user_feedback']}"
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Lead Agent的最终响应
        
        Args:
            content: LLM的最终回复
            tool_history: 工具调用历史
            
        Returns:
            格式化后的响应
        """
        # Lead Agent直接返回内容，因为已经在LLM中格式化
        # 工具调用历史可用于日志或调试
        
        if self.config.debug and tool_history:
            logger.debug(f"Lead Agent completed with {len(tool_history)} tool calls")
            for i, call in enumerate(tool_history, 1):
                logger.debug(f"  Tool {i}: {call['tool']} - Success: {call['result']['success']}")
        
        return content
    
    async def handle_user_feedback(
        self,
        feedback: str,
        context: Optional[Dict[str, Any]] = None
    ) -> AgentResponse:
        """
        处理用户反馈并调整研究方向
        
        Args:
            feedback: 用户反馈内容
            context: 当前上下文（包含task_plan和result）
            
        Returns:
            更新后的响应
        """
        # 构建带反馈的提示
        enhanced_context = context or {}
        enhanced_context["user_feedback"] = feedback
        
        # 构建指令
        instruction = f"""Based on the user feedback, please:
1. Review and adjust the task plan if needed
2. Identify what additional research is required
3. Execute the necessary updates
4. Provide a summary of changes made

User Feedback: {feedback}"""
        
        # 执行更新
        return await self.execute(instruction, enhanced_context)
    
    async def create_research_plan(
        self,
        research_topic: str,
        requirements: Optional[List[str]] = None
    ) -> AgentResponse:
        """
        创建研究计划的便捷方法
        
        Args:
            research_topic: 研究主题
            requirements: 具体要求列表
            
        Returns:
            包含task_plan的响应
        """
        # 构建创建计划的指令
        instruction = f"Create a comprehensive research plan for: {research_topic}"
        
        if requirements:
            instruction += "\n\nSpecific Requirements:\n"
            for req in requirements:
                instruction += f"- {req}\n"
        
        instruction += "\nPlease create a task_plan artifact with clear objectives and task breakdown."
        
        # 执行
        return await self.execute(instruction)
    
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
    创建Lead Agent实例的工厂函数
    
    Args:
        toolkit: 工具包
        
    Returns:
        配置好的Lead Agent实例
    """
    return LeadAgent(toolkit=toolkit)


if __name__ == "__main__":
    import asyncio
    
    async def test_lead_agent():
        """测试Lead Agent基础功能"""
        print("\n🧪 Testing Lead Agent")
        print("="*50)
        
        # 创建Lead Agent（不带工具，仅测试提示词生成）
        agent = create_lead_agent()
        
        # 测试1: 系统提示词生成
        print("\n📝 System Prompt (excerpt):")
        prompt = agent.build_system_prompt()
        print(prompt[:500] + "...")
        
        # 测试2: 带上下文的提示词
        print("\n📝 System Prompt with Context:")
        context = {
            "task_plan_content": "# Research Task: AI Safety\n## Tasks\n1. [✓] Literature review",
            "user_feedback": "Need more focus on alignment techniques"
        }
        prompt_with_context = agent.build_system_prompt(context)
        print(prompt_with_context[-500:])
        
        print("\n✅ Lead Agent tests completed")
    
    # 运行测试
    asyncio.run(test_lead_agent())