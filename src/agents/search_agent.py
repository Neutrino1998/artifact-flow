"""
Search Agent实现
负责信息检索和搜索优化
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from agents.base import BaseAgent, AgentConfig
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
                capabilities=["Web search", "Information retrieval"],
                required_tools=["web_search"],
                model="qwen3.5-flash-no-thinking",
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
        current_time = datetime.now().strftime("%Y/%m/%d %H:%M:%S %a")

        sections = [
            self._build_system_time(current_time),
            self._build_role_section(),
            self._build_task_plan_section(context),
            self._build_strategy_section(),
            self._build_output_format_section(),
        ]

        return "\n\n".join(s for s in sections if s)

    def _build_system_time(self, current_time: str) -> str:
        return f'<system_time>Current time: {current_time}</system_time>'

    def _build_role_section(self) -> str:
        return f"""<role>
You are {self.config.name}, a specialized search agent in a multi-agent team.

Execute targeted web searches to gather relevant, high-quality information. The Lead Agent coordinates overall strategy while you focus on information retrieval.
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

    def _build_strategy_section(self) -> str:
        return f"""<search_strategy>
- Start broad to understand the landscape, then refine with specific keywords
- Use date filters (freshness parameter) for recent information
- Try alternative phrasings if results are poor
- Maximum {self.config.max_tool_rounds} search iterations — stop when you have sufficient quality results
- Default to English for technical/academic content; use native language only for region-specific topics
- Prefer authoritative sources: Wikipedia, .edu, .gov, official documentation, established media, peer-reviewed publications
</search_strategy>"""

    def _build_output_format_section(self) -> str:
        return """<output_format>
Return findings in this structure:

<search_results>
  <result>
    <title>Page Title</title>
    <url>https://...</url>
    <content>Comprehensive and contextually relevant content</content>
  </result>
</search_results>
</output_format>"""

    def format_final_response(self, content: str) -> str:
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
