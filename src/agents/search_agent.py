"""
Search Agent实现
负责信息检索和搜索优化
"""

from typing import Dict, Any, Optional, List
from agents.base import BaseAgent, AgentConfig
from utils.logger import get_logger

logger = get_logger("SearchAgent")


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
                model="qwen-plus",
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
        prompt = f"""You are {self.config.name}, a specialized search agent with expertise in information retrieval.

## Your Mission

Execute targeted web searches to gather relevant, high-quality information.

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

## Output Format

Return your findings in this simple XML structure:

```xml
<search_results>
  <result>
    <title>Page Title</title>
    <url>https://...</url>
    <content>Key information and summary</content>
  </result>
  <!-- More results -->
</search_results>
```

## Search Guidelines

- Use 2-6 words for optimal search queries
- Start broad, then narrow down
- Maximum 3 search iterations (tool rounds)
- Focus on quality over quantity
- Extract and summarize key information from search results

## Tool Usage

You have access to the web_search tool with these parameters:
- query: Your search terms (required)
- freshness: Time filter - "oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit" (default)
- count: Number of results (1-50, default 10)"""
        
        # 添加任务上下文
        if context:
            if context.get("instruction"):
                prompt += f"\n\n## Current Task\n{context['instruction']}"
            
            if context.get("task_plan"):
                prompt += f"\n\n## Task Context\n{context['task_plan']}"
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Search Agent的最终响应
        
        Search Agent自己负责整理信息，直接返回其输出
        """
        return content
    
    async def search_with_refinement(
        self,
        initial_query: str,
        requirements: Optional[List[str]] = None,
        max_iterations: int = 3
    ) -> Dict[str, Any]:
        """
        执行带优化的搜索
        
        Args:
            initial_query: 初始搜索查询
            requirements: 搜索要求
            max_iterations: 最大搜索迭代次数
            
        Returns:
            搜索结果字典
        """
        context = {
            "instruction": f"Search for information about: {initial_query}",
            "requirements": requirements or []
        }
        
        response = await self.execute(
            f"Please search for: {initial_query}. Extract and summarize the key findings in the XML format.",
            context
        )
        
        return {
            "success": True,
            "findings": response.content,
            "tool_calls": response.tool_calls,
            "search_count": len([c for c in response.tool_calls 
                                if c.get("tool") == "web_search"])
        }


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