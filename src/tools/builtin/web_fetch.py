"""
Web内容抓取工具
基于Jina Reader API实现网页内容抓取，支持HTML和PDF文件
降级路径：HTML → BeautifulSoup纯文本提取，PDF → pypdf文本提取
"""

import asyncio
import os
import re
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger
import random

from bs4 import BeautifulSoup

from utils.doc_converter import DocConverter

logger = get_logger("ArtifactFlow")

# Jina Reader API配置
JINA_API_KEY = os.getenv("JINA_API_KEY")
JINA_BASE_URL = "https://r.jina.ai"
JINA_RETRY_MAX = 2
JINA_RETRY_DELAY = 30   # 429限额时等待秒数
JINA_TIMEOUT = 15        # 单次请求超时（秒），正常响应 1-5s


class WebFetchTool(BaseTool):
    """
    Web内容抓取工具
    使用Jina Reader API抓取网页内容并转换为Markdown格式
    支持HTML和PDF文件的统一处理

    特性：
    - Jina Reader API：统一处理HTML和PDF，返回clean markdown
    - 429重试：命中限额时自动等待重试
    - 智能降级：Jina失败后按类型降级（PDF → pypdf，HTML → BeautifulSoup）
    """

    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="Fetch and extract content from web pages and PDF files",
            permission=ToolPermission.CONFIRM
        )

        # User-Agent 池
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
        ]

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="url",
                type="string",
                description="URL to fetch (supports HTML and PDF)",
                required=True
            ),
            ToolParameter(
                name="max_content_length",
                type="integer",
                description="Maximum content length per page in characters (default: 20000)",
                required=False,
                default=20000
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        """
        执行网页抓取

        Args:
            url: 目标URL
            max_content_length: 最大内容长度

        Returns:
            ToolResult: 包含XML格式的抓取结果
        """
        url = params.get("url")
        if not url:
            return ToolResult(success=False, error="url parameter is required")

        # SSRF 防护：仅允许 http/https 协议
        if not url.lower().startswith(("http://", "https://")):
            return ToolResult(
                success=False,
                error=f"Unsupported URL scheme: {url}. Only http:// and https:// are allowed."
            )

        max_content_length = params["max_content_length"]

        logger.info(f"Fetching URL: {url}")

        try:
            result = await self._fetch_single_url(url, max_content_length)
            xml_result = self._format_result_to_xml(result)
            success = result.get("success", False)

            logger.info(f"Fetch {'succeeded' if success else 'failed'}: {url}")

            return ToolResult(
                success=success,
                data=xml_result,
                error=result.get("error") if not success else None,
            )

        except Exception as e:
            logger.exception(f"Fetch failed: {str(e)}")
            return ToolResult(success=False, error=f"Fetch failed: {str(e)}")

    def _detect_content_type(self, url: str) -> str:
        """
        通过 URL 后缀检测内容类型

        Args:
            url: 目标URL

        Returns:
            'pdf' 或 'html'
        """
        url_lower = url.lower().split('?')[0]  # 去掉查询参数
        if url_lower.endswith('.pdf'):
            return 'pdf'
        return 'html'

    async def _fetch_single_url(self, url: str, max_content_length: int) -> Dict[str, Any]:
        """
        抓取单个URL：先试Jina Reader API，失败后按类型降级

        Args:
            url: 目标URL
            max_content_length: 最大内容长度

        Returns:
            抓取结果字典
        """
        # 主路径：Jina Reader API
        jina_result = await self._fetch_via_jina(url, max_content_length)
        if jina_result is not None:
            return jina_result

        # 降级路径：按类型分别处理
        content_type = self._detect_content_type(url)
        if content_type == 'pdf':
            logger.info(f"Jina failed for PDF, falling back to pypdf: {url}")
            return await self._fetch_pdf(url, max_content_length)
        else:
            logger.info(f"Jina failed for HTML, falling back to BeautifulSoup: {url}")
            return await self._fetch_via_bs4(url, max_content_length)

    async def _fetch_via_jina(self, url: str, max_content_length: int) -> Optional[Dict[str, Any]]:
        """
        通过Jina Reader API抓取URL内容

        429时sleep(30)重试，最多重试JINA_RETRY_MAX次。
        返回None表示彻底失败，需走降级路径。

        Args:
            url: 目标URL
            max_content_length: 最大内容长度

        Returns:
            抓取结果字典，或None表示失败
        """
        jina_url = f"{JINA_BASE_URL}/{url}"
        headers = {
            "Accept": "text/markdown",
            "User-Agent": random.choice(self.user_agents),
        }
        if JINA_API_KEY:
            headers["Authorization"] = f"Bearer {JINA_API_KEY}"

        timeout = aiohttp.ClientTimeout(total=JINA_TIMEOUT)

        for attempt in range(1 + JINA_RETRY_MAX):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(jina_url, headers=headers, timeout=timeout) as response:
                        if response.status == 200:
                            content = await response.text()

                            # 提取标题（Jina返回的markdown第一行通常是 Title: xxx）
                            title = "Untitled"
                            title_match = re.match(r'^Title:\s*(.+)$', content, re.MULTILINE)
                            if title_match:
                                title = title_match.group(1).strip()

                            # 限制长度
                            if len(content) > max_content_length:
                                content = content[:max_content_length] + "\n\n[Content truncated...]"

                            source_type = self._detect_content_type(url)
                            logger.debug(f"Jina fetched {url}: {len(content)} chars")

                            return {
                                "success": True,
                                "url": url,
                                "title": title,
                                "content": content,
                                "word_count": len(content.split()),
                                "fetched_at": datetime.now().isoformat(),
                                "source_type": source_type,
                            }

                        elif response.status == 429:
                            if attempt < JINA_RETRY_MAX:
                                logger.warning(
                                    f"Jina 429 rate limit for {url}, "
                                    f"waiting {JINA_RETRY_DELAY}s (attempt {attempt + 1}/{JINA_RETRY_MAX})"
                                )
                                await asyncio.sleep(JINA_RETRY_DELAY)
                                continue
                            else:
                                logger.warning(f"Jina 429 exhausted retries for {url}")
                                return None

                        else:
                            logger.warning(f"Jina HTTP {response.status} for {url}")
                            return None

            except asyncio.TimeoutError:
                logger.warning(f"Jina timeout for {url} (attempt {attempt + 1})")
                if attempt < JINA_RETRY_MAX:
                    await asyncio.sleep(2)
                    continue
                return None
            except Exception as e:
                logger.warning(f"Jina error for {url}: {e}")
                return None

        return None

    async def _fetch_via_bs4(self, url: str, max_content_length: int) -> Dict[str, Any]:
        """
        降级路径：aiohttp下载HTML + BeautifulSoup提取纯文本

        Args:
            url: 目标URL
            max_content_length: 最大内容长度

        Returns:
            抓取结果字典
        """
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {"User-Agent": random.choice(self.user_agents)}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "url": url,
                            "error": f"HTTP {response.status}"
                        }

                    html = await response.text()

            # BeautifulSoup解析
            soup = BeautifulSoup(html, "html.parser")

            # 提取标题
            title = "Untitled"
            title_tag = soup.find("title")
            if title_tag and title_tag.string:
                title = title_tag.string.strip()

            # 移除无用标签
            for tag in soup.find_all(["script", "style", "nav", "header", "footer", "form", "aside"]):
                tag.decompose()

            # 提取纯文本
            content = soup.get_text(separator="\n")

            # 清理多余空行
            content = re.sub(r'\n{3,}', '\n\n', content).strip()

            # 限制长度
            if len(content) > max_content_length:
                content = content[:max_content_length] + "\n\n[Content truncated...]"

            logger.debug(f"BS4 fetched {url}: {len(content)} chars")

            return {
                "success": True,
                "url": url,
                "title": title,
                "content": content,
                "word_count": len(content.split()),
                "fetched_at": datetime.now().isoformat(),
                "source_type": "html",
            }

        except Exception as e:
            logger.warning(f"BS4 fetch failed for {url}: {e}")
            return {
                "success": False,
                "url": url,
                "error": f"Fetch failed: {str(e)}"
            }

    async def _fetch_pdf(self, url: str, max_content_length: int) -> Dict[str, Any]:
        """
        降级路径：抓取并解析PDF文件（DocConverter / pymupdf）

        Args:
            url: PDF文件URL
            max_content_length: 最大内容长度

        Returns:
            抓取结果字典
        """
        try:
            logger.info(f"Fetching PDF: {url}")

            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=timeout,
                    headers={'User-Agent': random.choice(self.user_agents)}
                ) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "url": url,
                            "error": f"HTTP {response.status}"
                        }

                    pdf_bytes = await response.read()

                    converter = DocConverter()
                    result = await converter.convert(pdf_bytes, "document.pdf")

                    content = result.content
                    if len(content) > max_content_length:
                        content = content[:max_content_length] + "\n\n[Content truncated...]"

                    page_count = result.metadata.get("page_count", 0)
                    logger.info(f"PDF extracted: {page_count} pages, {len(content)} chars")

                    return {
                        "success": True,
                        "url": url,
                        "title": "PDF Document",
                        "content": content,
                        "word_count": len(content.split()),
                        "fetched_at": datetime.now().isoformat(),
                        "source_type": "pdf",
                        "page_count": page_count,
                    }

        except Exception as e:
            logger.exception(f"PDF fetch failed for {url}")
            return {
                "success": False,
                "url": url,
                "error": f"PDF extraction failed: {str(e)}"
            }

    def _format_result_to_xml(self, result: Dict[str, Any]) -> str:
        """将单个抓取结果格式化为 XML"""
        if result.get("success"):
            source_type = result.get("source_type", "unknown")
            words = result["word_count"]
            attrs = f'type="{source_type}" words="{words}"'
            if result.get("page_count"):
                attrs += f' pages="{result["page_count"]}"'

            xml_parts = [f"<page {attrs}>"]
            xml_parts.append(f"  <url>{result['url']}</url>")
            xml_parts.append(f"  <title>{result.get('title', 'Untitled')}</title>")
            xml_parts.append(result["content"])
            xml_parts.append("</page>")
            return "\n".join(xml_parts)
        else:
            xml_parts = ["<error>"]
            xml_parts.append(f"  <url>{result['url']}</url>")
            xml_parts.append(f"  {result.get('error', 'Unknown error')}")
            xml_parts.append("</error>")
            return "\n".join(xml_parts)


if __name__ == "__main__":
    async def test():
        print("\nWeb Fetch Tool Test (Jina Reader API)")
        print("=" * 60)

        tool = WebFetchTool()

        # Test 1: HTML page
        print("\nTest 1: HTML page")
        result = await tool(url="https://github.com/Neutrino1998/artifact-flow")
        if result.success:
            print(f"OK: {len(result.data)} chars")
            print(result.data[:500] + "...")
        else:
            print(f"FAIL: {result.error}")

        # Test 2: PDF file
        print("\nTest 2: PDF file")
        result = await tool(url="https://arxiv.org/pdf/1706.03762.pdf", max_content_length=5000)
        if result.success:
            print(f"OK: {len(result.data)} chars")
            print(result.data[:500] + "...")
        else:
            print(f"FAIL: {result.error}")

    asyncio.run(test())
