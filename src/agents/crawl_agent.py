"""
Crawl Agent实现
负责网页内容抓取和信息提取
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from agents.base import BaseAgent, AgentConfig
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


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
                capabilities=[
                    "Deep content extraction",
                    "Web scraping",
                    "IMPORTANT: Instructions must include a specific URL to crawl"
                ],
                required_tools=["web_fetch"],
                model="qwen3-next-80b-instruct",  # 可以换成更便宜的模型
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
        # 获取系统时间
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")
        
        # 开始构建提示词
        prompt = f"""<system_time>IMPORTANT: Current time is "{current_time}"</system_time>

<agent_role>
You are {self.config.name}, a specialized agent for web content extraction and cleaning.

## Your Mission

Extract and clean valuable information from web pages.

## Team Context

You are part of a multi-agent team. The Lead Agent coordinates overall strategy while you focus on deep content extraction.
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
The following is our team's current task plan. Use this to understand what information is most valuable to extract:
<artifact id="task_plan" content_type="{task_plan_artifact['content_type']}" ...>
{task_plan_artifact['content']}
</artifact>
</team_task_plan>"""

        prompt += """

<core_capabilities>
## Core Capabilities

1. **Content Extraction**: Fetch and identify main content
2. **Content Cleaning**: Remove ads, navigation, and irrelevant sections
3. **Quality Assessment**: Detect anti-crawling, paywalls, or invalid content
4. **Concise Output**: Return only valuable information
</core_capabilities>

<extraction_process>
## Extraction Process

1. Fetch content from URLs
2. Assess content quality
3. Clean and extract key information
4. Format results
</extraction_process>

<output_format>
## Output Format

Return extracted content in this simple XML structure:

<extracted_pages>
  <page>
    <url>https://...</url>
    <title>Page Title</title>
    <content>Cleaned and extracted main content (be comprehensive and contextually relevant)</content>
  </page>
  <!-- More pages if needed -->
</extracted_pages>
</output_format>

<important_notes>
## Important Notes

- If content seems invalid (anti-crawling, paywall, error page), mention it in content field
- Focus on main content, skip navigation/ads/footers
- Keep content comprehensive andclose to original text
- Don't force extraction from clearly invalid pages
</important_notes>
"""
    
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Crawl Agent的最终响应
        
        Crawl Agent自己负责清洗和整理，直接返回其输出
        """
        return content

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