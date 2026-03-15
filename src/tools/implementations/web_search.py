"""
Web搜索工具
基于博查AI的搜索API实现
"""

import os
import json
import asyncio
import random
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime
from dotenv import load_dotenv

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger

# 加载环境变量
load_dotenv()

logger = get_logger("ArtifactFlow")

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
            permission=ToolPermission.AUTO
        )
        
        if not BOCHA_API_KEY:
            logger.warning("BOCHA_API_KEY not found in environment variables")
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description=(
                    "Search query using keywords."
                    "Note: Does not support search operators like 'site:', 'AND', 'OR', quotes, or minus signs."
                ),
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
    
    _MAX_RETRIES = 3
    _BASE_DELAY = 2.0

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
        # 获取参数（默认值已由 _apply_defaults 填充）
        query = params.get("query")
        freshness = params["freshness"]
        count = min(params["count"], 50)  # 限制最大50条

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

        last_error: str = ""
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return await self._do_search(headers, payload, query)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = str(e)
                if attempt < self._MAX_RETRIES:
                    delay = self._BASE_DELAY * (2 ** attempt) * (0.5 + random.random())
                    logger.warning(
                        f"Search attempt {attempt + 1} failed ({type(e).__name__}), "
                        f"retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)

        logger.error(f"Search failed after {self._MAX_RETRIES + 1} attempts: {last_error}")
        return ToolResult(success=False, error=f"Search failed after retries: {last_error}")

    # HTTP status codes that are transient and worth retrying
    _RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    async def _do_search(
        self, headers: Dict[str, str], payload: Dict[str, Any], query: str
    ) -> ToolResult:
        """Execute a single search request. Raises on retryable errors."""
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

                    # Retryable HTTP errors — raise to trigger retry loop
                    if response.status in self._RETRYABLE_STATUS_CODES:
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"Search API error: {response.status}",
                        )

                    # Non-retryable HTTP errors — return immediately
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
    
    def _format_results_to_xml(self, data: Dict[str, Any]) -> str:
        """将搜索结果格式化为 XML（元数据用 attribute，重内容用子元素）"""
        web_pages = data.get("webPages", {})
        results = web_pages.get("value", [])

        if not results:
            return "<search_results>No results found</search_results>"

        xml_parts = ["<search_results>"]

        for result in results:
            title = result.get("name", "")
            url = result.get("url", "")
            site_name = result.get("siteName", "")

            # 处理日期（修正时区问题）
            date = result.get("datePublished", "")
            if not date:
                date = result.get("dateLastCrawled", "")
                if date and date.endswith("Z"):
                    date = date[:-1] + "+08:00"

            # 元数据 → attributes
            attrs = f'url="{url}" title="{title}" site="{site_name}"'
            if date:
                attrs += f' date="{date}"'

            snippet = result.get("snippet", "")
            summary = result.get("summary", "")

            # 重内容 → 子元素
            xml_parts.append(f"  <result {attrs}>")
            xml_parts.append(f"    <snippet>{snippet}</snippet>")
            if summary:
                xml_parts.append(f"    <summary>{summary}</summary>")
            xml_parts.append("  </result>")

        xml_parts.append("</search_results>")

        return "\n".join(xml_parts)


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