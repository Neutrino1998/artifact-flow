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
                model="qwen3.5-flash-no-thinking",
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
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")

        sections = [
            self._build_system_time(current_time),
            self._build_role_section(),
            self._build_task_plan_section(context),
            self._build_extraction_section(),
            self._build_output_format_section(),
        ]

        return "\n\n".join(s for s in sections if s)

    def _build_system_time(self, current_time: str) -> str:
        return f'<system_time>Current time: {current_time}</system_time>'

    def _build_role_section(self) -> str:
        return f"""<role>
You are {self.config.name}, a specialized agent for web content extraction and cleaning in a multi-agent team.

Extract and clean valuable information from web pages. The Lead Agent coordinates overall strategy while you focus on deep content extraction.
</role>"""

    def _build_task_plan_section(self, context: Optional[Dict[str, Any]]) -> Optional[str]:
        if not context or not context.get("artifacts_inventory"):
            return None

        task_plan_artifact = next(
            (a for a in context["artifacts_inventory"] if a["id"] == "task_plan" and a.get("content")),
            None
        )
        if not task_plan_artifact:
            return None

        return f"""<team_task_plan>
{task_plan_artifact['content']}
</team_task_plan>"""

    def _build_extraction_section(self) -> str:
        return """<extraction_guidelines>
- Focus on main content — skip navigation, ads, and footers
- Keep content comprehensive and close to original text
- If content seems invalid (anti-crawling, paywall, error page), note it in the content field
- Don't force extraction from clearly invalid pages
</extraction_guidelines>"""

    def _build_output_format_section(self) -> str:
        return """<output_format>
Return extracted content in this structure:

<extracted_pages>
  <page>
    <url>https://...</url>
    <title>Page Title</title>
    <content>Cleaned and extracted main content</content>
  </page>
</extracted_pages>
</output_format>"""

    def format_final_response(self, content: str) -> str:
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
