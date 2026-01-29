"""
Web内容抓取工具
基于crawl4ai实现网页内容的深度抓取，支持HTML和PDF文件
"""

import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime
from io import BytesIO

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger
import random

# 导入crawl4ai组件
try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

# 导入PDF处理
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

logger = get_logger("ArtifactFlow")


class WebFetchTool(BaseTool):
    """
    Web内容抓取工具
    使用crawl4ai深度抓取网页内容并转换为结构化格式
    支持HTML和PDF文件的智能检测和处理
    
    特性：
    - 智能类型检测：自动识别HTML/PDF/其他文件类型
    - HTML抓取：使用crawl4ai进行深度内容提取和清洗
    - PDF处理：使用pypdf提取PDF文本内容
    - 内存自适应：通过MemoryAdaptiveDispatcher控制并发浏览器实例数
    - 防止内存爆炸：每个HTML页面会启动一个浏览器实例，严格控制并发保护服务器
    """
    
    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="Fetch and extract content from web pages and PDF files",
            permission=ToolPermission.AUTO
        )
        
        if not CRAWL4AI_AVAILABLE:
            logger.error("crawl4ai is not available")
            return
        
        if not PDF_SUPPORT:
            logger.warning("pypdf is not installed. PDF support disabled. Install with: pip install pypdf")
        
        # User-Agent 池（扩展版）
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
        
        # 初始化浏览器配置（用于HTML）
        self.browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            user_agent=random.choice(self.user_agents)  # 随机 User-Agent
        )
        
        # 内容过滤器配置
        self.prune_filter = PruningContentFilter(
            threshold=0.45,              # 适中的阈值，平衡内容质量和数量
            threshold_type="dynamic",   # 动态调整阈值
            # 注意：不设置min_word_threshold，避免过滤掉主体内容
        )
        
        # Markdown生成器配置
        self.md_generator = DefaultMarkdownGenerator(
            options={
                "ignore_links": True,      # 移除超链接
                "ignore_images": True,      # 移除图片
                "escape_html": True,        # 转义HTML实体
                "skip_internal_links": True # 跳过内部链接
            },
            content_filter=self.prune_filter
        )
        
        # 运行配置
        self.run_config = CrawlerRunConfig(
            # 内容过滤
            word_count_threshold=100,  # 降低阈值，保留更多内容
            excluded_tags=['form', 'header', 'footer', 'nav'],  
            exclude_external_links=True,
            # 内容处理
            process_iframes=True,
            remove_overlay_elements=True,
            # 缓存控制
            cache_mode=CacheMode.DISABLED,  # 禁用缓存，保证获取最新内容
            # Markdown生成器
            markdown_generator=self.md_generator,
            # 禁用日志
            verbose=False
        )
    
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
                description="Maximum content length per page in characters (default: 10000)",
                required=False,
                default=10000
            ),
            ToolParameter(
                name="max_concurrent",
                type="integer",
                description="Maximum concurrent browser instances (default: 3, max: 5) - Each browser uses ~100-300MB memory",
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
            max_concurrent: 最大并发浏览器实例数

        Returns:
            ToolResult: 包含XML格式的抓取结果
        """
        if not CRAWL4AI_AVAILABLE:
            return ToolResult(
                success=False,
                error="crawl4ai is not installed. Please install it first."
            )
        
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
        
        max_content_length = params.get("max_content_length", 10000)
        max_concurrent = min(params.get("max_concurrent", 3), 5)  # 限制最大5个
        
        logger.info(f"Fetching {len(urls)} URL(s) with max {max_concurrent} concurrent browsers")
        
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
    
    async def _detect_content_type(self, url: str) -> str:
        """
        检测URL的内容类型
        
        Args:
            url: 目标URL
            
        Returns:
            'pdf', 'html', 或 'unknown'
        """
        # 1. 先通过URL后缀快速判断
        url_lower = url.lower()
        if url_lower.endswith('.pdf'):
            return 'pdf'
        elif any(url_lower.endswith(ext) for ext in ['.html', '.htm', '.php', '.asp', '.jsp']):
            return 'html'
        
        # 2. 发送HEAD请求检查Content-Type
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.head(
                    url, 
                    timeout=timeout,
                    allow_redirects=True,
                    headers={'User-Agent': random.choice(self.user_agents)}
                ) as response:
                    content_type = response.headers.get('Content-Type', '').lower()
                    
                    if 'pdf' in content_type or 'application/pdf' in content_type:
                        return 'pdf'
                    elif any(t in content_type for t in ['html', 'text/html', 'text/plain']):
                        return 'html'
                    
        except Exception as e:
            logger.warning(f"HEAD request failed for {url}: {e}, assuming HTML")
        
        # 3. 默认按HTML处理
        return 'html'
    
    async def _fetch_pdf(self, url: str, max_content_length: int) -> Dict[str, Any]:
        """
        抓取并解析PDF文件
        
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
    
    async def _fetch_html_urls(
        self,
        urls: List[str],
        max_content_length: int,
        max_concurrent: int = 3
    ) -> List[Dict[str, Any]]:
        """
        使用crawl4ai抓取HTML页面（使用MemoryAdaptiveDispatcher保护内存）
        
        Args:
            urls: HTML URL列表
            max_content_length: 最大内容长度
            max_concurrent: 最大并发数
            
        Returns:
            抓取结果列表
        """
        # 创建内存自适应调度器 - 防止内存爆炸
        dispatcher = MemoryAdaptiveDispatcher(
            memory_threshold_percent=70.0,  # 内存使用率超过70%时暂停
            check_interval=1.0,  # 每秒检查一次内存
            max_session_permit=max_concurrent,  # 最大并发浏览器实例数
            memory_wait_timeout=120.0,  # 超时120秒抛出错误
            rate_limiter=RateLimiter(
                base_delay=(0.5, 1.0),  # 基础延迟0.5-1秒
                max_delay=10.0,  # 最大延迟10秒
                max_retries=2  # 最多重试2次
            ),
        )
        
        logger.info(f"Using MemoryAdaptiveDispatcher: max {max_concurrent} concurrent sessions, memory threshold 70%")
        
        # 使用crawl4ai的批量抓取 + dispatcher
        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            # arun_many 会自动使用 dispatcher 控制并发
            crawl_results = await crawler.arun_many(
                urls=urls,
                config=self.run_config,
                dispatcher=dispatcher  # 传入内存控制器
            )
            
            # 处理结果
            results = []
            for i, result in enumerate(crawl_results):
                url = urls[i]
                
                if result.success:
                    # 提取内容 - 使用新的 markdown 属性
                    content = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
                    
                    # 限制长度
                    if len(content) > max_content_length:
                        content = content[:max_content_length] + "\n\n[Content truncated...]"
                    
                    results.append({
                        "success": True,
                        "url": url,
                        "title": result.metadata.get("title", "Untitled") if result.metadata else "Untitled",
                        "content": content,
                        "word_count": len(content.split()),
                        "fetched_at": datetime.now().isoformat(),
                        "source_type": "html"
                    })
                    
                    logger.debug(f"Successfully fetched {url}: {len(content)} chars")
                else:
                    results.append({
                        "success": False,
                        "url": url,
                        "error": f"Crawl failed: {result.error_message or 'Unknown error'}"
                    })
                    
                    logger.warning(f"Failed to fetch {url}: {result.error_message}")
        
        return results
    
    async def _fetch_urls(
        self,
        urls: List[str],
        max_content_length: int,
        max_concurrent: int = 3
    ) -> List[Dict[str, Any]]:
        """
        抓取多个URL（智能检测类型并分别处理）
        
        Args:
            urls: URL列表
            max_content_length: 最大内容长度
            max_concurrent: 最大并发浏览器实例数
            
        Returns:
            抓取结果列表
        """
        # 步骤1: 检测所有URL的类型
        logger.info("Detecting content types...")
        content_types = await asyncio.gather(*[
            self._detect_content_type(url) for url in urls
        ])
        
        # 步骤2: 按类型分类URL
        pdf_urls = []
        html_urls = []
        
        for url, content_type in zip(urls, content_types):
            if content_type == 'pdf':
                pdf_urls.append(url)
                logger.info(f"Detected as PDF: {url}")
            else:
                html_urls.append(url)
                logger.info(f"Detected as HTML: {url}")
        
        results = []
        
        # 步骤3: 处理PDF文件（并发）
        if pdf_urls:
            logger.info(f"Fetching {len(pdf_urls)} PDF file(s)...")
            pdf_results = await asyncio.gather(*[
                self._fetch_pdf(url, max_content_length) for url in pdf_urls
            ])
            results.extend(pdf_results)
        
        # 步骤4: 处理HTML页面（使用crawl4ai + 内存自适应调度）
        if html_urls:
            logger.info(f"Fetching {len(html_urls)} HTML page(s)...")
            html_results = await self._fetch_html_urls(html_urls, max_content_length, max_concurrent)
            results.extend(html_results)
        
        return results
    
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
        print("\n🧪 Web抓取工具测试（支持PDF）")
        print("="*60)
        
        if not CRAWL4AI_AVAILABLE:
            print("❌ crawl4ai未安装")
            return
        
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
        # 使用一个公开的PDF测试
        pdf_urls = ["https://arxiv.org/pdf/1706.03762.pdf"]  # Attention is All You Need论文
        
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