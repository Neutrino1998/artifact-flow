"""
Crawl Agent实现
负责网页内容抓取和信息提取
"""

from typing import Dict, Any, Optional, List
from agents.base import BaseAgent, AgentConfig
from utils.logger import get_logger

logger = get_logger("Agents")


class CrawlAgent(BaseAgent):
    """
    Crawl Agent - 内容抓取专家
    
    核心能力：
    1. 深度内容抓取：从指定URL提取详细信息
    2. 智能内容清洗：去除无关内容，保留核心信息
    3. 质量判断：识别反爬、无效内容等情况
    4. 简洁输出：只返回有价值的信息
    
    工具配置：
    - web_fetch: 网页内容抓取工具
    """
    
    def __init__(self, config: Optional[AgentConfig] = None, toolkit=None):
        """
        初始化Crawl Agent
        
        Args:
            config: Agent配置
            toolkit: 工具包（应包含web_fetch工具）
        """
        if not config:
            config = AgentConfig(
                name="crawl_agent",
                description="Web content extraction and cleaning specialist",
                model="qwen-flash",  # 可以换成更便宜的模型
                temperature=0.3,  # 更低温度for精确提取
                max_tool_rounds=2,  # 通常1-2轮即可
                streaming=True
            )
        
        super().__init__(config, toolkit)
    
    def build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """
        构建Crawl Agent的系统提示词
        
        Args:
            context: 包含URL列表和提取要求的上下文
            
        Returns:
            系统提示词
        """
        prompt = f"""You are {self.config.name}, a specialized agent for web content extraction and cleaning.

## Your Mission

Extract and clean valuable information from web pages.

## Team Context

You are part of a multi-agent research team. The Lead Agent coordinates overall strategy while you focus on deep content extraction."""

        # 🌟 新增：如果有task_plan，添加团队目标
        if context and context.get("task_plan_content"):
            prompt += f"""

## Team Task Plan

The following is our team's current task plan. Use this to understand what information is most valuable to extract:

{context['task_plan_content']}

**Your Role**: Extract detailed content that supports this plan, focusing on relevant sections."""

        prompt += """

## Core Capabilities

1. **Content Extraction**: Fetch and identify main content
2. **Content Cleaning**: Remove ads, navigation, and irrelevant sections
3. **Quality Assessment**: Detect anti-crawling, paywalls, or invalid content
4. **Concise Output**: Return only valuable information

## Extraction Process

1. Fetch content from URLs
2. Assess content quality
3. Clean and extract key information
4. Format results

## Output Format

Return extracted content in this simple XML structure:

```xml
<extracted_pages>
  <page>
    <url>https://...</url>
    <title>Page Title</title>
    <content>Cleaned and extracted main content</content>
  </page>
  <!-- More pages if needed -->
</extracted_pages>
```

## Important Notes

- If content seems invalid (anti-crawling, paywall, error page), mention it in content field
- Focus on main content, skip navigation/ads/footers
- Keep content concise but informative
- Don't force extraction from clearly invalid pages

## Tool Usage

You have access to the web_fetch tool with these parameters:
- urls: Single URL or list of URLs (required)
- max_content_length: Maximum content per page (default 5000)
- max_concurrent: Concurrent fetches (default 3, max 5)"""
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Crawl Agent的最终响应
        
        Crawl Agent自己负责清洗和整理，直接返回其输出
        """
        return content
    
    async def extract_from_urls(
        self,
        urls: List[str],
        focus_areas: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        从URL列表提取内容
        
        Args:
            urls: 要抓取的URL列表
            focus_areas: 关注的内容领域
            
        Returns:
            提取结果字典
        """
        context = {
            "urls": urls,
            "focus_areas": focus_areas or []
        }
        
        instruction = f"Please extract and clean content from the following {len(urls)} URL(s)."
        if focus_areas:
            instruction += f" Focus on: {', '.join(focus_areas)}"
        instruction += " Return cleaned content in the XML format."
        
        response = await self.execute(instruction, context)
        
        return {
            "success": True,
            "extracted_content": response.content,
            "tool_calls": response.tool_calls,
            "pages_processed": len([c for c in response.tool_calls 
                                   if c.get("tool") == "web_fetch"])
        }


# 工厂函数
def create_crawl_agent(toolkit=None) -> CrawlAgent:
    """
    创建Crawl Agent实例
    
    Args:
        toolkit: 包含web_fetch工具的工具包
        
    Returns:
        配置好的Crawl Agent实例
    """
    return CrawlAgent(toolkit=toolkit)