"""
Web内容抓取工具
基于Jina Reader API实现网页内容抓取，支持HTML和PDF文件
降级路径：HTML → BeautifulSoup纯文本提取，PDF → pypdf文本提取
"""

import asyncio
import os
import re
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime
from io import BytesIO

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger
import random

from bs4 import BeautifulSoup

# 导入PDF处理
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

logger = get_logger("ArtifactFlow")

# Jina Reader API配置
JINA_API_KEY = os.getenv("JINA_API_KEY")
JINA_BASE_URL = "https://r.jina.ai"
JINA_RETRY_MAX = 2
JINA_RETRY_DELAY = 30  # 429限额时等待秒数


class WebFetchTool(BaseTool):
    """
    Web内容抓取工具
    使用Jina Reader API抓取网页内容并转换为Markdown格式
    支持HTML和PDF文件的统一处理

    特性：
    - Jina Reader API：统一处理HTML和PDF，返回clean markdown
    - 429重试：命中限额时自动等待重试
    - 智能降级：Jina失败后按类型降级（PDF → pypdf，HTML → BeautifulSoup）
    - 并发控制：Semaphore控制最大并发请求数
    """

    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="Fetch and extract content from web pages and PDF files",
            permission=ToolPermission.CONFIRM
        )

        if not PDF_SUPPORT:
            logger.warning("pypdf is not installed. PDF fallback disabled. Install with: pip install pypdf")

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

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="url_list",
                type="array[string]",
                description="URL or list of URLs to fetch (supports HTML and PDF)",
                required=True
            ),
            ToolParameter(
                name="max_content_length",
                type="integer",
                description="Maximum content length per page in characters (default: 20000)",
                required=False,
                default=20000
            ),
            ToolParameter(
                name="max_concurrent",
                type="integer",
                description="Maximum concurrent fetch requests (default: 3, max: 5)",
                required=False,
                default=3
            )
        ]

    async def execute(self, **params) -> ToolResult:
        """
        执行网页抓取

        Args:
            url_list: URL字符串或URL列表
            max_content_length: 每页最大内容长度
            max_concurrent: 最大并发请求数

        Returns:
            ToolResult: 包含XML格式的抓取结果
        """
        # 参数处理
        urls_param = params.get("url_list")
        if not urls_param:
            return ToolResult(success=False, error="url_list parameter is required")

        # 确保urls是列表
        if isinstance(urls_param, str):
            urls = [urls_param]
        elif isinstance(urls_param, list):
            urls = urls_param
        else:
            return ToolResult(success=False, error="url_list must be string or list")

        # SSRF 防护：仅允许 http/https 协议
        for url in urls:
            if not url.lower().startswith(("http://", "https://")):
                return ToolResult(
                    success=False,
                    error=f"Unsupported URL scheme: {url}. Only http:// and https:// are allowed."
                )

        # 默认值已由 _apply_defaults 填充
        max_content_length = params["max_content_length"]
        max_concurrent = max(1, min(params["max_concurrent"], 5))  # 限制1-5

        logger.info(f"Fetching {len(urls)} URL(s) with max {max_concurrent} concurrent requests")

        try:
            # 执行抓取
            results = await self._fetch_urls(urls, max_content_length, max_concurrent)

            # 格式化为XML
            xml_result = self._format_results_to_xml(results)

            # 统计信息
            success_count = sum(1 for r in results if r.get("success"))

            logger.info(f"Fetch completed: {success_count}/{len(urls)} successful")

            return ToolResult(
                success=True,
                data=xml_result,
                metadata={
                    "total_urls": len(urls),
                    "success_count": success_count,
                    "failed_count": len(urls) - success_count
                }
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

    async def _fetch_urls(
        self,
        urls: List[str],
        max_content_length: int,
        max_concurrent: int = 3
    ) -> List[Dict[str, Any]]:
        """
        抓取多个URL（统一走Jina，失败后按类型降级）

        Args:
            urls: URL列表
            max_content_length: 最大内容长度
            max_concurrent: 最大并发请求数

        Returns:
            抓取结果列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited_fetch(url: str) -> Dict[str, Any]:
            async with semaphore:
                return await self._fetch_single_url(url, max_content_length)

        results = await asyncio.gather(*[_limited_fetch(url) for url in urls])
        return list(results)

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

        timeout = aiohttp.ClientTimeout(total=60)

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
        降级路径：抓取并解析PDF文件（pypdf）

        Args:
            url: PDF文件URL
            max_content_length: 最大内容长度

        Returns:
            抓取结果字典
        """
        if not PDF_SUPPORT:
            return {
                "success": False,
                "url": url,
                "error": "PDF support not available. Install pypdf: pip install pypdf"
            }

        try:
            logger.info(f"Fetching PDF: {url}")

            timeout = aiohttp.ClientTimeout(total=60)  # PDF可能较大，60秒超时
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

                    # 检查Content-Type
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'pdf' not in content_type:
                        logger.warning(f"Expected PDF but got {content_type}, trying anyway...")

                    # 读取PDF内容
                    pdf_bytes = await response.read()

                    # 使用pypdf提取文本
                    pdf_file = BytesIO(pdf_bytes)
                    pdf_reader = PdfReader(pdf_file)

                    # 提取所有页面文本
                    text_parts = []
                    for page_num, page in enumerate(pdf_reader.pages, 1):
                        try:
                            page_text = page.extract_text()
                            if page_text.strip():
                                text_parts.append(page_text)
                        except Exception as e:
                            logger.warning(f"Failed to extract page {page_num}: {e}")

                    full_text = "\n\n".join(text_parts)

                    # 获取PDF元数据
                    title = "PDF Document"
                    if pdf_reader.metadata:
                        title = pdf_reader.metadata.get('/Title', title)

                    # 限制长度
                    if len(full_text) > max_content_length:
                        full_text = full_text[:max_content_length] + "\n\n[Content truncated...]"

                    logger.info(f"PDF extracted: {len(text_parts)} pages, {len(full_text)} chars")

                    return {
                        "success": True,
                        "url": url,
                        "title": title,
                        "content": full_text,
                        "word_count": len(full_text.split()),
                        "fetched_at": datetime.now().isoformat(),
                        "source_type": "pdf",
                        "page_count": len(pdf_reader.pages)
                    }

        except Exception as e:
            logger.exception(f"PDF fetch failed for {url}")
            return {
                "success": False,
                "url": url,
                "error": f"PDF extraction failed: {str(e)}"
            }

    def _format_results_to_xml(self, results: List[Dict[str, Any]]) -> str:
        """
        将抓取结果格式化为XML

        Args:
            results: 抓取结果列表

        Returns:
            XML格式字符串
        """
        xml_parts = ["<fetch_results>"]

        for result in results:
            if result.get("success"):
                # 成功的结果
                xml_parts.append("  <fetch_result>")
                xml_parts.append(f"    <url>{self._escape_xml(result['url'])}</url>")
                xml_parts.append(f"    <title>{self._escape_xml(result.get('title', 'Untitled'))}</title>")
                xml_parts.append(f"    <source_type>{result.get('source_type', 'unknown')}</source_type>")

                # PDF特有字段
                if result.get('page_count'):
                    xml_parts.append(f"    <page_count>{result['page_count']}</page_count>")

                xml_parts.append(f"    <content>{self._escape_xml(result['content'])}</content>")
                xml_parts.append(f"    <word_count>{result['word_count']}</word_count>")
                xml_parts.append(f"    <fetched_at>{result['fetched_at']}</fetched_at>")
                xml_parts.append("  </fetch_result>")
            else:
                # 失败的结果
                xml_parts.append("  <fetch_error>")
                xml_parts.append(f"    <url>{self._escape_xml(result['url'])}</url>")
                xml_parts.append(f"    <error>{self._escape_xml(result.get('error', 'Unknown error'))}</error>")
                xml_parts.append("  </fetch_error>")

        xml_parts.append("</fetch_results>")

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


if __name__ == "__main__":
    # 测试代码
    async def test():
        print("\n🧪 Web抓取工具测试（Jina Reader API）")
        print("="*60)

        tool = WebFetchTool()

        # 测试1: HTML页面
        print("\n📄 测试1: HTML页面抓取")
        test_urls = ["https://github.com/Neutrino1998/artifact-flow"]

        result = await tool(url_list=test_urls)

        if result.success:
            print(f"✅ HTML抓取成功")
            print(f"   成功: {result.metadata['success_count']}/{result.metadata['total_urls']}")
            print("\nXML结果（前1000字符）:")
            print(result.data[:1000] + "...")
        else:
            print(f"❌ 抓取失败: {result.error}")

        # 测试2: PDF文件
        print("\n📑 测试2: PDF文件抓取")
        pdf_urls = ["https://arxiv.org/pdf/1706.03762.pdf"]

        result = await tool(url_list=pdf_urls, max_content_length=5000)

        if result.success:
            print(f"✅ PDF抓取成功")
            print(f"   成功: {result.metadata['success_count']}/{result.metadata['total_urls']}")
            print("\nXML结果（前1000字符）:")
            print(result.data[:1000] + "...")
        else:
            print(f"❌ 抓取失败: {result.error}")

        # 测试3: 混合抓取
        print("\n🔀 测试3: 混合抓取（HTML + PDF）")
        mixed_urls = [
            "https://www.python.org",
            "https://arxiv.org/pdf/1706.03762.pdf"
        ]

        result = await tool(
            url_list=mixed_urls,
            max_content_length=3000,
            max_concurrent=2
        )

        if result.success:
            print(f"✅ 混合抓取完成")
            print(f"   成功: {result.metadata['success_count']}/{result.metadata['total_urls']}")
            print(f"   失败: {result.metadata['failed_count']}")
        else:
            print(f"❌ 抓取失败: {result.error}")

    # 运行测试
    asyncio.run(test())
