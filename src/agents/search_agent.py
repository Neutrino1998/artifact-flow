"""
Search Agent实现
负责信息检索和搜索优化
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from agents.base import BaseAgent, AgentConfig
from click import prompt
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class SearchAgent(BaseAgent):
    """
    Search Agent - 信息检索专家
    
    核心能力：
    1. 自主搜索优化：根据结果质量调整搜索策略
    2. 多轮迭代搜索：通过refine关键词提高搜索质量
    3. 结构化输出：返回简洁的搜索结果
    4. 智能判断：知道何时停止搜索
    
    工具配置：
    - web_search: 网页搜索工具
    """
    
    def __init__(self, config: Optional[AgentConfig] = None, toolkit=None):
        """
        初始化Search Agent
        
        Args:
            config: Agent配置
            toolkit: 工具包（应包含web_search工具）
        """
        if not config:
            config = AgentConfig(
                name="search_agent",
                description="Web search and information retrieval specialist",
                model="qwen3-next-80b-instruct",
                temperature=0.5,  # 较低温度for精确搜索
                max_tool_rounds=3,  # 最多3轮搜索优化
                streaming=True
            )
        
        super().__init__(config, toolkit)
    
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建Search Agent的系统提示词
        
        Args:
            context: 包含task指令和task_plan的上下文
            
        Returns:
            系统提示词
        """
        # 获取系统时间
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        
        # 开始构建提示词
        prompt = f"""<system_time>IMPORTANT: Current time is "{current_time}"</system_time>

<agent_role>
You are {self.config.name}, a specialized search agent with expertise in information retrieval.

## Your Mission

Execute targeted web searches to gather relevant, high-quality information.

## Team Context

You are part of a multi-agent research team. The Lead Agent coordinates overall strategy while you focus on information retrieval.
</agent_role>"""

        # 如果有task_plan，添加团队目标
        if context and context.get("artifacts_inventory"):
            task_plan_artifact = next(
                (a for a in context["artifacts_inventory"] if a["id"] == "task_plan"),
                None
            )
            
            if task_plan_artifact:
                prompt += f"""
<team_task_plan>
The following is our team's current task plan. Use this to understand the broader context of your search tasks:
<artifact id="task_plan" content_type="{task_plan_artifact['content_type']}" ...>
{task_plan_artifact['content']}
</artifact>
</team_task_plan>"""

        prompt += """

<core_capabilities>
## Core Capabilities

1. **Smart Search Strategy**
   - Start with broad searches to understand the landscape
   - Refine queries based on initial results
   - Use specific terms and filters when needed
   - Know when you have sufficient information

2. **Search Refinement Techniques**
   - Add specific keywords for precision
   - Use date filters for recent information (freshness parameter)
   - Try alternative phrasings if results are poor

3. **Quality Assessment**
   - Relevance to the task
   - Source credibility
   - Information recency
</core_capabilities>

<output_format>
## Output Format

Return your relevant findings in this simple XML structure:

<search_results>
  <result>
    <title>Page Title</title>
    <url>https://...</url>
    <content>Comprehensive and contextually relevant content</content>
  </result>
  <!-- More results -->
</search_results>
</output_format>

<search_guidelines>
## Search Guidelines

- Use 2-6 words for optimal search queries
- Start broad, then narrow down
- Maximum 3 search iterations (tool rounds)
- Quality over quantity: select only credible sources
- Return comprehensive content close to original text
- Default to English sources for technical/academic content; prioritize native language sources only when topic is region-specific
</search_guidelines>
"""
    
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Search Agent的最终响应
        
        Search Agent自己负责整理信息，直接返回其输出
        """
        return content

# 工厂函数
def create_search_agent(toolkit=None) -> SearchAgent:
    """
    创建Search Agent实例
    
    Args:
        toolkit: 包含web_search工具的工具包
        
    Returns:
        配置好的Search Agent实例
    """
    return SearchAgent(toolkit=toolkit)