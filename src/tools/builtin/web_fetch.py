"""
Web内容抓取工具
基于Jina Reader API实现网页内容抓取
文件类 URL(.pdf 等 WEB_FETCH_BLOB_SUFFIXES 尾缀)在 Jina 之前走直连 blob 旁路
降级路径：Jina 失败 → BeautifulSoup 纯文本提取
"""

import asyncio
import os
import re
import aiohttp
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, unquote

from tools.base import ArtifactSpec, BaseTool, ToolResult, ToolPermission
from config import config
from utils.logger import get_logger
from utils.time import utc_now
from utils.url_guard import validate_public_url, SsrfBlockedError
import random

from bs4 import BeautifulSoup


logger = get_logger("ArtifactFlow")


class _ResponseTooLargeError(Exception):
    """fallback 下载体超过 WEB_FETCH_MAX_BYTES。"""


async def _read_capped(response, max_bytes: int) -> bytes:
    """流式读取响应体并封顶字节数。

    先查声明的 Content-Length(诚实大响应早退),再分块累计实际(解压后)字节,
    超限即中断 —— 防 gzip 炸弹 / 超大响应打爆 worker 内存。
    """
    if response.content_length is not None and response.content_length > max_bytes:
        raise _ResponseTooLargeError(
            f"Content-Length {response.content_length} exceeds cap {max_bytes}"
        )

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise _ResponseTooLargeError(f"Body exceeded cap {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)

# Jina Reader API配置
JINA_API_KEY = os.getenv("JINA_API_KEY")
JINA_BASE_URL = "https://r.jina.ai"
JINA_RETRY_MAX = 2
JINA_RETRY_DELAY = 30   # 429限额时等待秒数
JINA_TIMEOUT = 30        # 单次请求超时（秒），正常响应 1-5s，不重试所以给足余量


def _parse_html_with_bs4(raw: bytes) -> tuple[str, str]:
    """
    Sync HTML parse: returns (title, plain_text_content).
    Designed to run inside asyncio.to_thread — BS4 is CPU bound.
    """
    soup = BeautifulSoup(raw, "html.parser")

    title = "Untitled"
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        title = title_tag.string.strip()

    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "form", "aside"]):
        tag.decompose()

    content = soup.get_text(separator="\n")
    content = re.sub(r'\n{3,}', '\n\n', content).strip()

    return title, content


class WebFetchTool(BaseTool):
    """
    Web内容抓取工具
    使用Jina Reader API抓取网页内容并转换为Markdown格式
    文件类 URL(blob 尾缀)在 Jina 之前走直连 blob 旁路

    特性：
    - Jina Reader API：网页 → clean markdown
    - 429重试：命中限额时自动等待重试
    - 降级：Jina 失败 → BeautifulSoup 纯文本提取
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

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "URL to fetch (supports HTML and PDF)",
                }
            },
            "required": ["url"],
            "additionalProperties": False,
        }

    async def execute(self, **params) -> ToolResult:
        """
        执行网页抓取

        Args:
            url: 目标URL

        Returns:
            ToolResult: 包含XML格式的抓取结果。超长内容由引擎中间件按
            max_result_size_chars 自动落盘到 artifact，本工具不再截断。
        """
        url = params.get("url")
        if not url:
            return ToolResult(success=False, error="url parameter is required")

        # SSRF 防护：scheme 限 http(s) + 主机解析后必须全部指向公网。
        # fallback 路径另设 allow_redirects=False，杜绝 302 → 内网 绕过本次校验。
        try:
            await validate_public_url(url)
        except SsrfBlockedError as e:
            logger.warning(f"web_fetch blocked non-public URL: {e}")
            return ToolResult(
                success=False,
                error="URL is not an allowed public address",
            )

        logger.info(f"Fetching URL: {url}")

        try:
            result = await self._fetch_single_url(url)

            # 文件旁路:二进制结果声明为 artifact,由引擎中间件落盘 + 回填句柄
            # (见 ArtifactSpec / engine._maybe_persist_tool_result)。
            if result.get("is_blob"):
                if not result.get("success"):
                    logger.info(f"Fetch failed (blob): {url}")
                    return ToolResult(
                        success=False,
                        error=result.get("error", "File download failed"),
                    )
                blob = result["blob"]
                content_type = result["content_type"]
                spec = ArtifactSpec(
                    content_type=content_type,
                    filename=result["filename"],
                    title=result["filename"],
                    content="",  # 二进制不抽文本
                    blob=blob,
                    metadata={"source_url": url, "fetched_at": result["fetched_at"]},
                )
                # 占位 data:引擎 _maybe_persist_tool_result 两路都会替换它(落盘成功
                # → 预览,失败 → error),模型任何现有路径都看不到 —— 仅防 artifact
                # 中间件被旁路时裸 blob 无说明。
                note = (
                    f'<file url="{url}" content_type="{content_type}" '
                    f'bytes="{len(blob)}">Downloaded binary file; stored as artifact.</file>'
                )
                logger.info(f"Fetch succeeded (blob): {url}")
                return ToolResult(success=True, data=note, artifact=spec)

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

    @staticmethod
    def _url_path_lower(url: str) -> str:
        """URL → 小写、去查询参数/片段的路径,尾缀判断的**唯一归一化**。

        _detect_content_type 与 _blob_route_for_url 共用 —— 两处各自手写时
        已漂移过一次(`#`-strip 只有一边有),归一化不一致 = 同一 URL 两处
        判型不同。
        """
        return url.lower().split('?')[0].split('#')[0]

    def _detect_content_type(self, url: str) -> str:
        """
        通过 URL 后缀检测内容类型(仅用于 Jina 结果的 source_type 标注;
        默认配置下 .pdf ∈ blob 尾缀、在 Jina 之前已被旁路截走,'pdf' 仅在
        operator 从 WEB_FETCH_BLOB_SUFFIXES 移除 .pdf 时可达)

        Returns:
            'pdf' 或 'html'
        """
        if self._url_path_lower(url).endswith('.pdf'):
            return 'pdf'
        return 'html'

    def _blob_route_for_url(self, url: str) -> Optional[Tuple[str, str]]:
        """文件类 URL 尾缀 → (suffix, content_type 兜底);非文件类返回 None。

        命中即走直连 blob 旁路(Jina 之前),不抽文本——Jina 对二进制本就坏。
        """
        path = self._url_path_lower(url)
        for suffix, mime in config.WEB_FETCH_BLOB_SUFFIXES.items():
            if path.endswith(suffix):
                return suffix, mime
        return None

    def _filename_from_url(self, url: str, fallback_suffix: str) -> str:
        """从 URL 末段取下载文件名;缺失/无扩展名时用 download<suffix> 兜底。"""
        path = urlparse(url).path
        name = unquote(path.rsplit('/', 1)[-1]) if path else ""
        if not name or '.' not in name:
            name = f"download{fallback_suffix}"
        return name

    async def _fetch_single_url(self, url: str) -> Dict[str, Any]:
        """
        抓取单个URL：文件类尾缀直连 blob 旁路;否则先试 Jina,失败后按类型降级

        Args:
            url: 目标URL

        Returns:
            抓取结果字典
        """
        # 文件旁路:文件类尾缀在 Jina 之前分流为直连下载(blob,不抽文本)。
        blob_route = self._blob_route_for_url(url)
        if blob_route is not None:
            suffix, fallback_mime = blob_route
            logger.info(f"File-type URL, routing to blob bypass: {url}")
            return await self._fetch_file_as_blob(url, suffix, fallback_mime)

        # 主路径：Jina Reader API
        jina_result = await self._fetch_via_jina(url)
        if jina_result is not None:
            return jina_result

        # Jina 失败 → 即将本机直连 fallback。入口的 validate_public_url 与此刻之间隔了
        # Jina 最坏 ~60s(429 sleep / timeout),DNS-rebinding 窗口被放大且时机可控
        # (攻击者让 Jina 失败再翻 DNS)。直连前再校验一次,把窗口收回到函数调用级 ms。
        try:
            await validate_public_url(url)
        except SsrfBlockedError as e:
            logger.warning(f"web_fetch fallback blocked non-public URL: {e}")
            return {
                "success": False,
                "url": url,
                "error": "URL is not an allowed public address",
            }

        # 降级路径:直连 + BeautifulSoup 抽文本。真 PDF 到不了这里(`.pdf` ∈
        # WEB_FETCH_BLOB_SUFFIXES,在 Jina 之前已走 blob 旁路),旧 pypdf 降级分支已删。
        logger.info(f"Jina failed, falling back to BeautifulSoup: {url}")
        return await self._fetch_via_bs4(url)

    async def _fetch_via_jina(self, url: str) -> Optional[Dict[str, Any]]:
        """
        通过Jina Reader API抓取URL内容

        429时sleep(30)重试，最多重试JINA_RETRY_MAX次。
        返回None表示彻底失败，需走降级路径。

        Args:
            url: 目标URL

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

                            source_type = self._detect_content_type(url)
                            # 提到 INFO 与"工具完成/外部调用结果"一致,事故诊断必需。
                            # 大体积内容不打,只打来源/尺寸(对齐日志分级原则)。
                            logger.info(f"Jina fetched {url}: {len(content)} chars")

                            return {
                                "success": True,
                                "url": url,
                                "title": title,
                                "content": content,
                                "word_count": len(content.split()),
                                "fetched_at": utc_now().isoformat(),
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
                logger.warning(f"Jina timeout for {url}, skipping retries")
                return None
            except Exception as e:
                logger.warning(f"Jina error for {url}: {e}")
                return None

        return None

    async def _fetch_via_bs4(self, url: str) -> Dict[str, Any]:
        """
        降级路径：aiohttp下载HTML + BeautifulSoup提取纯文本

        Args:
            url: 目标URL

        Returns:
            抓取结果字典
        """
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {"User-Agent": random.choice(self.user_agents)}

            # allow_redirects=False：不跟随重定向，杜绝 302 → 内网 / 元数据 绕过 pre-flight
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "url": url,
                            "error": f"HTTP {response.status}"
                        }

                    # 流式读取并封顶字节（防 gzip 炸弹 / 大响应 OOM）
                    raw = await _read_capped(response, config.WEB_FETCH_MAX_BYTES)

            # BeautifulSoup 解析是 CPU bound（大页面可达数百 ms），丢线程池避免卡 event loop
            title, content = await asyncio.to_thread(_parse_html_with_bs4, raw)

            # 提到 INFO,与 Jina 路径对称(都是外部调用结果)。
            logger.info(f"BS4 fetched {url}: {len(content)} chars")

            return {
                "success": True,
                "url": url,
                "title": title,
                "content": content,
                "word_count": len(content.split()),
                "fetched_at": utc_now().isoformat(),
                "source_type": "html",
            }

        except _ResponseTooLargeError as e:
            logger.warning(f"BS4 fetch too large for {url}: {e}")
            return {"success": False, "url": url, "error": "Response too large"}
        except Exception as e:
            # str(e) 可能含内网地址/连接细节，不回显给 LLM（SSRF-06）
            logger.warning(f"BS4 fetch failed for {url}: {e}")
            return {"success": False, "url": url, "error": "Failed to fetch content"}

    async def _fetch_file_as_blob(
        self, url: str, suffix: str, fallback_mime: str
    ) -> Dict[str, Any]:
        """文件类 URL 直连下载为 blob(不抽文本)。

        **SSRF**:本旁路在 Jina 之前直连,故 ``_fetch_single_url`` 末尾的二次校验在此
        分支被**跳过**;入口 ``validate_public_url`` 与此刻之间仍有 DNS-rebinding 窗口。
        因此直连前必须自带一次校验 + ``allow_redirects=False``(杜绝 302 → 内网/元数据)。
        """
        try:
            await validate_public_url(url)
        except SsrfBlockedError as e:
            logger.warning(f"web_fetch blob bypass blocked non-public URL: {e}")
            return {
                "success": False,
                "url": url,
                "is_blob": True,
                "error": "URL is not an allowed public address",
            }

        try:
            logger.info(f"Fetching file (blob bypass): {url}")
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=timeout,
                    headers={'User-Agent': random.choice(self.user_agents)},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return {
                            "success": False,
                            "url": url,
                            "is_blob": True,
                            "error": f"HTTP {response.status}",
                        }

                    # 流式读取并封顶字节(防 gzip 炸弹 / 大响应 OOM)
                    blob = await _read_capped(response, config.WEB_FETCH_MAX_BYTES)

            # content_type **不信远端 Content-Type 头**:它是不可信输入,会流进 artifact
            # 的 XML 属性(`type="..."`)与 /raw 服务的 MIME —— 恶意服务器可借此让 envelope
            # 非良构,或对 .png URL 回 image/svg+xml 制造 stored-XSS。我们只在 URL 尾缀命中
            # 受控映射(WEB_FETCH_BLOB_SUFFIXES)时才进本旁路,故尾缀 MIME 即权威且安全。
            content_type = fallback_mime

            filename = self._filename_from_url(url, suffix)
            logger.info(f"Downloaded {url}: {len(blob)} bytes, {content_type}")
            return {
                "success": True,
                "url": url,
                "is_blob": True,
                "blob": blob,
                "content_type": content_type,
                "filename": filename,
                "fetched_at": utc_now().isoformat(),
                "source_type": "file",
            }

        except _ResponseTooLargeError as e:
            logger.warning(f"File too large for {url}: {e}")
            return {"success": False, "url": url, "is_blob": True, "error": "File too large"}
        except Exception as e:
            # 不回显 str(e)(可能含内网地址/路径),仅入 server 日志(SSRF-06)
            logger.exception(f"File download failed for {url}")
            return {"success": False, "url": url, "is_blob": True, "error": "File download failed"}

    def _format_result_to_xml(self, result: Dict[str, Any]) -> str:
        """将单个抓取结果格式化为 XML"""
        if result.get("success"):
            source_type = result.get("source_type", "unknown")
            words = result["word_count"]
            attrs = f'type="{source_type}" words="{words}"'

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
        result = await tool(url="https://arxiv.org/pdf/1706.03762.pdf")
        if result.success:
            print(f"OK: {len(result.data)} chars")
            print(result.data[:500] + "...")
        else:
            print(f"FAIL: {result.error}")

    asyncio.run(test())
