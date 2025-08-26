"""
Web搜索工具
基于博查AI的搜索API实现
"""

import os
import json
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger
from utils.retry import api_retry

# 加载环境变量
load_dotenv()

logger = get_logger("WebSearch")

# 博查AI配置
BOCHA_API_KEY = os.getenv("BOCHA_API_KEY")
BOCHA_API_URL = "https://api.bochaai.com/v1/web-search"


class WebSearchTool(BaseTool):
    """
    Web搜索工具
    使用博查AI搜索引擎进行网页搜索
    """
    
    def __init__(self):
        super().__init__(
            name="web_search",
            description="Search the web for information using Bocha AI search engine",
            permission=ToolPermission.PUBLIC
        )
        
        if not BOCHA_API_KEY:
            logger.warning("BOCHA_API_KEY not found in environment variables")
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="Search query string",
                required=True
            ),
            ToolParameter(
                name="freshness",
                type="string",
                description="Time range filter: 'noLimit'(default), 'oneDay', 'oneWeek', 'oneMonth', 'oneYear'",
                required=False,
                default="noLimit"
            ),
            ToolParameter(
                name="count",
                type="integer",
                description="Number of results to return (1-50, default: 10)",
                required=False,
                default=10
            )
        ]
    
    @api_retry()  # 使用重试装饰器处理网络错误
    async def execute(self, **params) -> ToolResult:
        """
        执行搜索
        
        Args:
            query: 搜索查询
            freshness: 时间范围过滤
            count: 返回结果数量
            
        Returns:
            ToolResult: 包含XML格式的搜索结果
        """
        # 获取参数
        query = params.get("query")
        freshness = params.get("freshness", "noLimit")
        count = min(params.get("count", 10), 50)  # 限制最大50条
        
        if not query:
            return ToolResult(success=False, error="Query parameter is required")
        
        if not BOCHA_API_KEY:
            return ToolResult(
                success=False,
                error="BOCHA_API_KEY not configured. Please set it in .env file"
            )
        
        # 准备请求
        headers = {
            "Authorization": f"Bearer {BOCHA_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "freshness": freshness,
            "summary": True,  # 启用摘要
            "count": count
        }
        
        logger.info(f"Searching for: {query} (freshness: {freshness}, count: {count})")
        
        try:
            # 执行搜索请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    BOCHA_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Search API error: {response.status} - {error_text}")
                        
                        # 处理特定错误
                        if response.status == 403:
                            return ToolResult(
                                success=False,
                                error="API quota exceeded or insufficient balance"
                            )
                        elif response.status == 401:
                            return ToolResult(
                                success=False,
                                error="Invalid API key"
                            )
                        else:
                            return ToolResult(
                                success=False,
                                error=f"Search API error: {response.status}"
                            )
                    
                    # 解析响应
                    result = await response.json()
                    
                    if result.get("code") != 200:
                        return ToolResult(
                            success=False,
                            error=f"Search failed: {result.get('message', 'Unknown error')}"
                        )
                    
                    # 格式化结果为XML
                    xml_result = self._format_results_to_xml(result.get("data", {}))
                    
                    # 记录统计
                    web_pages = result.get("data", {}).get("webPages", {})
                    total_matches = web_pages.get("totalEstimatedMatches", 0)
                    actual_results = len(web_pages.get("value", []))
                    
                    logger.info(
                        f"Search completed: {actual_results} results from ~{total_matches:,} matches"
                    )
                    
                    return ToolResult(
                        success=True,
                        data=xml_result,
                        metadata={
                            "query": query,
                            "total_matches": total_matches,
                            "results_count": actual_results,
                            "log_id": result.get("log_id")
                        }
                    )
                    
        except asyncio.TimeoutError:
            logger.error("Search request timeout")
            return ToolResult(success=False, error="Search request timeout")
        except Exception as e:
            logger.exception(f"Search failed: {str(e)}")
            return ToolResult(success=False, error=f"Search failed: {str(e)}")
    
    def _format_results_to_xml(self, data: Dict[str, Any]) -> str:
        """
        将搜索结果格式化为XML
        
        Args:
            data: 博查API返回的data字段
            
        Returns:
            XML格式的搜索结果
        """
        # 提取网页结果
        web_pages = data.get("webPages", {})
        results = web_pages.get("value", [])
        
        if not results:
            return "<search_results>\n  <message>No results found</message>\n</search_results>"
        
        # 构建XML
        xml_parts = ["<search_results>"]
        
        for result in results:
            # 清理和转义XML特殊字符
            title = self._escape_xml(result.get("name", ""))
            url = self._escape_xml(result.get("url", ""))
            snippet = self._escape_xml(result.get("snippet", ""))
            summary = self._escape_xml(result.get("summary", ""))
            site_name = self._escape_xml(result.get("siteName", ""))
            
            # 处理日期（修正时区问题）
            date_published = result.get("datePublished", "")
            if not date_published:
                # 使用dateLastCrawled作为备用
                date_published = result.get("dateLastCrawled", "")
                if date_published and date_published.endswith("Z"):
                    # 修正时区：将Z替换为+08:00
                    date_published = date_published[:-1] + "+08:00"
            
            # 构建单个结果的XML
            xml_parts.append("  <search_result>")
            xml_parts.append(f"    <title>{title}</title>")
            xml_parts.append(f"    <url>{url}</url>")
            xml_parts.append(f"    <snippet>{snippet}</snippet>")
            
            # 只有当有摘要时才添加
            if summary:
                xml_parts.append(f"    <summary>{summary}</summary>")
            
            xml_parts.append(f"    <site_name>{site_name}</site_name>")
            
            if date_published:
                xml_parts.append(f"    <date_published>{date_published}</date_published>")
            
            xml_parts.append("  </search_result>")
        
        xml_parts.append("</search_results>")
        
        return "\n".join(xml_parts)
    
    def _escape_xml(self, text: str) -> str:
        """
        转义XML特殊字符
        
        Args:
            text: 原始文本
            
        Returns:
            转义后的文本
        """
        if not text:
            return ""
        
        # XML特殊字符转义
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


# 注册工具的便捷函数
def register_web_search_tool():
    """注册Web搜索工具"""
    from tools.registry import register_tool
    
    register_tool(WebSearchTool())
    logger.info("Registered web search tool")


if __name__ == "__main__":
    # 测试代码
    async def test():
        print("\n🧪 Web搜索工具测试")
        print("="*50)
        
        # 检查API Key
        if not BOCHA_API_KEY:
            print("❌ 请先在.env文件中设置BOCHA_API_KEY")
            print("   获取地址: https://open.bochaai.com")
            return
        
        # 创建工具
        tool = WebSearchTool()
        
        # 测试1: 基本搜索
        print("\n📍 测试1: 基本搜索")
        result = await tool(query="人工智能最新进展 2024")
        
        if result.success:
            print(f"✅ 搜索成功： {result.metadata.get('results_count')} 条结果")
            print("\n搜索结果（前2000字符）:")
            print(result.data[:2000] + "...")
        else:
            print(f"❌ 搜索失败: {result.error}")
        
        # 测试2: 带时间范围的搜索
        print("\n📍 测试2: 最近一周的搜索")
        result = await tool(
            query="OpenAI GPT",
            freshness="oneWeek",
            count=5
        )
        
        if result.success:
            print(f"✅ 搜索成功： {result.metadata.get('results_count')} 条结果")
        else:
            print(f"❌ 搜索失败: {result.error}")
        
        # 测试3: 错误处理
        print("\n📍 测试3: 错误处理（空查询）")
        result = await tool(query="")
        print(f"   预期失败: {'✅' if not result.success else '❌'}")
        if not result.success:
            print(f"   错误信息: {result.error}")
    
    # 运行测试
    asyncio.run(test())