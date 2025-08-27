"""
Crawl Agent实现
负责网页内容抓取和信息提取
"""

from typing import Dict, Any, Optional, List
from agents.base import BaseAgent, AgentConfig
from utils.logger import get_logger

logger = get_logger("CrawlAgent")


class CrawlAgent(BaseAgent):
    """
    Crawl Agent - 内容抓取专家
    
    核心能力：
    1. 深度内容抓取：从指定URL提取详细信息
    2. 智能内容清洗：去除无关内容，保留核心信息
    3. 结构化提取：识别并提取关键信息点
    4. 质量优先：注重内容质量而非数量
    
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
                description="Web content extraction and analysis specialist",
                model="qwen-plus",
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
        prompt = f"""You are {self.config.name}, a specialized agent for web content extraction and analysis.

## Your Mission

Extract, clean, and structure valuable information from web pages for research purposes.

## Core Capabilities

### 1. Content Extraction
- Fetch complete page content
- Identify main article/content area
- Extract text, data, and key information
- Preserve important context and relationships

### 2. Content Cleaning
- Remove navigation, ads, and boilerplate
- Filter out irrelevant sections
- Keep only substantive content
- Maintain logical flow and structure

### 3. Information Structuring
Extract and organize:
- Main topics and themes
- Key facts and figures
- Important quotes and statements
- Data points and statistics
- Conclusions and insights

### 4. Quality Assessment
Evaluate content based on:
- Relevance to research topic
- Information density
- Source authority
- Content freshness
- Factual accuracy

## Extraction Process

1. **Receive URLs**: Get list of pages to analyze
2. **Fetch Content**: Use web_fetch tool
3. **Analyze Structure**: Understand page organization
4. **Extract Key Info**: Pull out relevant information
5. **Format Results**: Structure findings in XML

## Output Format

Return extracted content in this XML structure:

```xml
<extraction_results>
  <summary>Overview of extracted content</summary>
  <pages>
    <page>
      <url>https://...</url>
      <title>Page Title</title>
      <extracted_at>2024-XX-XX</extracted_at>
      <key_content>
        <section name="Main Topic">
          <p>Important paragraph or section content</p>
          <facts>
            <fact>Key fact or data point</fact>
            <fact>Another important finding</fact>
          </facts>
        </section>
        <!-- More sections -->
      </key_content>
      <metadata>
        <author>If available</author>
        <publish_date>If available</publish_date>
        <word_count>Approximate count</word_count>
      </metadata>
    </page>
    <!-- More pages -->
  </pages>
  <extraction_stats>
    <total_pages>X</total_pages>
    <successful>X</successful>
    <failed>X</failed>
  </extraction_stats>
</extraction_results>
```

## Extraction Guidelines

- Focus on substantive content over metadata
- Preserve important context and relationships
- Summarize long sections while keeping key points
- Maintain factual accuracy - don't infer or add information
- Handle failed fetches gracefully

## Tool Usage

You have access to the web_fetch tool with these parameters:
- urls: Single URL or list of URLs (required)
- max_content_length: Maximum content per page (default 5000)
- max_concurrent: Concurrent fetches (default 3, max 5)

## Special Instructions

- Quality over quantity: Better to extract less but more relevant content
- Don't attempt to fetch the same URL multiple times
- If content is behind paywall or restricted, note it and move on
- For very long pages, focus on the most relevant sections"""
        
        # 添加任务上下文
        if context:
            if context.get("urls"):
                prompt += "\n\n## URLs to Process"
                for url in context["urls"]:
                    prompt += f"\n- {url}"
            
            if context.get("focus_areas"):
                prompt += "\n\n## Focus Areas"
                for area in context["focus_areas"]:
                    prompt += f"\n- {area}"
            
            if context.get("task_plan"):
                prompt += f"\n\n## Research Context\n{context['task_plan']}"
        
        return prompt
    
    def format_final_response(self, content: str, tool_history: List[Dict]) -> str:
        """
        格式化Crawl Agent的最终响应为XML
        
        Args:
            content: LLM的分析和总结
            tool_history: 抓取工具调用历史
            
        Returns:
            XML格式的提取结果
        """
        # 如果content已经是XML格式，直接返回
        if "<extraction_results>" in content:
            return content
        
        # 否则构建标准XML响应
        xml_parts = ["<extraction_results>"]
        
        # 添加摘要
        xml_parts.append(f"  <summary>{self._extract_summary(content)}</summary>")
        
        # 添加抓取的页面内容
        xml_parts.append("  <pages>")
        
        successful = 0
        failed = 0
        
        for call in tool_history:
            if call["tool"] == "web_fetch":
                result = call["result"]
                if result["success"]:
                    successful += 1
                    # 提取和格式化内容
                    fetch_data = result.get("data", "")
                    xml_parts.append(self._format_fetch_data(fetch_data, call["params"]))
                else:
                    failed += 1
        
        xml_parts.append("  </pages>")
        
        # 添加统计信息
        xml_parts.append("  <extraction_stats>")
        xml_parts.append(f"    <total_pages>{successful + failed}</total_pages>")
        xml_parts.append(f"    <successful>{successful}</successful>")
        xml_parts.append(f"    <failed>{failed}</failed>")
        xml_parts.append("  </extraction_stats>")
        
        xml_parts.append("</extraction_results>")
        
        return "\n".join(xml_parts)
    
    def _extract_summary(self, content: str) -> str:
        """从内容中提取摘要"""
        lines = content.strip().split('\n')
        summary = lines[0] if lines else content
        
        if len(summary) > 300:
            summary = summary[:297] + "..."
        
        return summary
    
    def _format_fetch_data(self, fetch_data: str, params: Dict) -> str:
        """格式化抓取数据为XML片段"""
        # 解析fetch_data中的内容
        xml_parts = ["    <page>"]
        
        # 获取URL
        urls = params.get("urls", [])
        if isinstance(urls, str):
            urls = [urls]
        
        if urls:
            xml_parts.append(f"      <url>{urls[0]}</url>")
        
        # 尝试从fetch_data提取标题和内容
        if isinstance(fetch_data, str):
            # 简单提取（实际应该解析XML）
            if "<title>" in fetch_data:
                import re
                title_match = re.search(r'<title>(.*?)</title>', fetch_data)
                if title_match:
                    xml_parts.append(f"      <title>{title_match.group(1)}</title>")
            else:
                xml_parts.append("      <title>Untitled</title>")
            
            # 提取内容（简化版）
            xml_parts.append("      <key_content>")
            
            # 截取主要内容
            content_preview = fetch_data[:1000] if len(fetch_data) > 1000 else fetch_data
            xml_parts.append(f"        <section name=\"Main Content\">")
            xml_parts.append(f"          <p>{self._clean_text(content_preview)}</p>")
            xml_parts.append(f"        </section>")
            
            xml_parts.append("      </key_content>")
        
        xml_parts.append("    </page>")
        
        return "\n".join(xml_parts)
    
    def _clean_text(self, text: str) -> str:
        """清理文本内容"""
        # 移除多余的空白和特殊字符
        import re
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # 转义XML特殊字符
        replacements = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&apos;"
        }
        
        for char, escaped in replacements.items():
            text = text.replace(char, escaped)
        
        return text
    
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
        
        # 构建指令
        instruction = f"Please extract and analyze content from the following {len(urls)} URL(s)."
        if focus_areas:
            instruction += f" Focus on: {', '.join(focus_areas)}"
        
        # 使用基类的execute方法
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


if __name__ == "__main__":
    import asyncio
    
    async def test_crawl_agent():
        """测试Crawl Agent基础功能"""
        print("\n🧪 Testing Crawl Agent")
        print("="*50)
        
        # 创建Crawl Agent
        agent = create_crawl_agent()
        
        # 测试1: 系统提示词
        print("\n📝 System Prompt (excerpt):")
        context = {
            "urls": ["https://example.com/article1", "https://example.com/article2"],
            "focus_areas": ["Key findings", "Statistical data"],
            "task_plan": "Research on AI safety"
        }
        prompt = agent.build_system_prompt(context)
        print(prompt[:800] + "...")
        
        # 测试2: 响应格式化
        print("\n📝 Response Formatting:")
        mock_tool_history = [
            {
                "tool": "web_fetch",
                "params": {"urls": ["https://example.com"]},
                "result": {
                    "success": True,
                    "data": "<title>Test Page</title><content>Sample content here</content>"
                }
            }
        ]
        formatted = agent.format_final_response("Extracted content from web page", mock_tool_history)
        print(formatted[:600])
        
        print("\n✅ Crawl Agent tests completed")
    
    # 运行测试
    asyncio.run(test_crawl_agent())