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
    3. 结构化输出：返回标准XML格式的搜索结果
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

Execute targeted web searches to gather relevant, high-quality information for research tasks.

## Core Capabilities

### 1. Smart Search Strategy
- Start with broad searches to understand the landscape
- Refine queries based on initial results
- Use specific terms and filters when needed
- Know when you have sufficient information

### 2. Search Refinement Techniques
- Add specific keywords for precision
- Use date filters for recent information (freshness parameter)
- Combine related concepts
- Try alternative phrasings if results are poor

### 3. Quality Assessment
Evaluate search results based on:
- Relevance to the research question
- Source credibility
- Information recency
- Content depth and detail

## Search Execution Process

1. **Analyze Task**: Understand what information is needed
2. **Initial Search**: Start with core keywords
3. **Evaluate Results**: Assess quality and coverage
4. **Refine if Needed**: Adjust search terms for better results
5. **Compile Findings**: Organize results in structured format

## Output Format

Return your findings in this XML structure:

```xml
<search_findings>
  <summary>Brief overview of what was found</summary>
  <search_results>
    <result>
      <title>Page Title</title>
      <url>https://...</url>
      <snippet>Key information excerpt</snippet>
      <relevance>High/Medium/Low</relevance>
      <key_points>
        <point>Important finding 1</point>
        <point>Important finding 2</point>
      </key_points>
    </result>
    <!-- More results -->
  </search_results>
  <search_strategy>
    <queries_used>List of search queries attempted</queries_used>
    <refinements>Any query refinements made</refinements>
  </search_strategy>
</search_findings>
```

## Search Guidelines

- Use 2-6 words for optimal search queries
- Start broad, then narrow down
- Maximum 3 search iterations (tool rounds)
- Focus on quality over quantity
- Stop when you have sufficient relevant information

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
                prompt += f"\n\n## Research Context\n{context['task_plan']}"
            
            if context.get("requirements"):
                prompt += "\n\n## Specific Requirements"
                for req in context["requirements"]:
                    prompt += f"\n- {req}"
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Search Agent的最终响应为XML
        
        Args:
            content: LLM的分析和总结
            tool_history: 搜索工具调用历史
            
        Returns:
            XML格式的搜索结果
        """
        # 如果content已经是XML格式，直接返回
        if "<search_findings>" in content:
            return content
        
        # 否则构建标准XML响应
        xml_parts = ["<search_findings>"]
        
        # 添加摘要
        xml_parts.append(f"  <summary>{self._extract_summary(content)}</summary>")
        
        # 添加搜索结果（从工具历史中提取）
        xml_parts.append("  <search_results>")
        
        for call in tool_history:
            if call["tool"] == "web_search" and call["result"]["success"]:
                # 解析搜索结果
                search_data = call["result"].get("data", "")
                xml_parts.append(self._format_search_data(search_data))
        
        xml_parts.append("  </search_results>")
        
        # 添加搜索策略信息
        xml_parts.append("  <search_strategy>")
        xml_parts.append("    <queries_used>")
        
        for call in tool_history:
            if call["tool"] == "web_search":
                query = call["params"].get("query", "")
                xml_parts.append(f"      <query>{query}</query>")
        
        xml_parts.append("    </queries_used>")
        xml_parts.append("    <total_searches>{}</total_searches>".format(
            len([c for c in tool_history if c["tool"] == "web_search"])
        ))
        xml_parts.append("  </search_strategy>")
        
        xml_parts.append("</search_findings>")
        
        return "\n".join(xml_parts)
    
    def _extract_summary(self, content: str) -> str:
        """从内容中提取摘要"""
        # 简单实现：取前200个字符或第一段
        lines = content.strip().split('\n')
        summary = lines[0] if lines else content
        
        if len(summary) > 200:
            summary = summary[:197] + "..."
        
        return summary
    
    def _format_search_data(self, search_data: str) -> str:
        """格式化搜索数据为XML片段"""
        # 这里应该解析search_data中的XML并重新格式化
        # 简化实现：直接返回相关部分
        if isinstance(search_data, str) and "<search_result>" in search_data:
            # 提取search_result标签内容
            import re
            results = re.findall(r'<search_result>.*?</search_result>', 
                                search_data, re.DOTALL)
            return '\n'.join(f"    {r}" for r in results[:5])  # 最多5个结果
        
        return "    <!-- No structured results available -->"
    
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
        
        # 使用基类的execute方法，它会处理工具调用循环
        response = await self.execute(
            f"Please search for: {initial_query}",
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


if __name__ == "__main__":
    import asyncio
    
    async def test_search_agent():
        """测试Search Agent基础功能"""
        print("\n🧪 Testing Search Agent")
        print("="*50)
        
        # 创建Search Agent
        agent = create_search_agent()
        
        # 测试1: 系统提示词
        print("\n📝 System Prompt (excerpt):")
        context = {
            "instruction": "Find recent developments in quantum computing",
            "requirements": ["Focus on 2024 breakthroughs", "Include commercial applications"]
        }
        prompt = agent.build_system_prompt(context)
        print(prompt[:800] + "...")
        
        # 测试2: 响应格式化
        print("\n📝 Response Formatting:")
        mock_tool_history = [
            {
                "tool": "web_search",
                "params": {"query": "quantum computing 2024"},
                "result": {
                    "success": True,
                    "data": "<search_result><title>Test</title></search_result>"
                }
            }
        ]
        formatted = agent.format_final_response("Found information about quantum computing", mock_tool_history)
        print(formatted[:500])
        
        print("\n✅ Search Agent tests completed")
    
    # 运行测试
    asyncio.run(test_search_agent())