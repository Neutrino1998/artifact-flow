"""
Web内容抓取工具
基于crawl4ai实现网页内容的深度抓取
"""

import asyncio
from typing import List, Dict, Any, Optional, Union
from datetime import datetime

from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission
from utils.logger import get_logger
from utils.retry import api_retry
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
    logger = get_logger("Tools")
    logger.warning("crawl4ai not installed. Install with: pip install crawl4ai")

logger = get_logger("Tools")


class WebFetchTool(BaseTool):
    """
    Web内容抓取工具
    使用crawl4ai深度抓取网页内容并转换为结构化格式
    
    特性：
    - 内存自适应：通过MemoryAdaptiveDispatcher控制并发浏览器实例数
    - 防止内存爆炸：每个URL会启动一个浏览器实例，需要严格控制并发
    - 降级处理：当内存控制器不可用时，降级为顺序抓取
    """
    
    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="Fetch and extract content from web pages",
            permission=ToolPermission.PUBLIC
        )
        
        if not CRAWL4AI_AVAILABLE:
            logger.error("crawl4ai is not available")
            return
        
        # 新增：User-Agent 池
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        
        # 初始化浏览器配置
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
                name="urls",
                type="array[string]",
                description="URL or list of URLs to fetch",
                required=True
            ),
            ToolParameter(
                name="max_content_length",
                type="integer",
                description="Maximum content length per page in characters (default: 5000)",
                required=False,
                default=5000
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
            urls: URL字符串或URL列表
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
        urls_param = params.get("urls")
        if not urls_param:
            return ToolResult(success=False, error="urls parameter is required")
        
        # 确保urls是列表
        if isinstance(urls_param, str):
            urls = [urls_param]
        elif isinstance(urls_param, list):
            urls = urls_param
        else:
            return ToolResult(success=False, error="urls must be string or list")
        
        max_content_length = params.get("max_content_length", 5000)
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
    
    async def _fetch_urls(
        self,
        urls: List[str],
        max_content_length: int,
        max_concurrent: int = 3
    ) -> List[Dict[str, Any]]:
        """
        抓取多个URL
        
        Args:
            urls: URL列表
            max_content_length: 最大内容长度
            max_concurrent: 最大并发浏览器实例数
            
        Returns:
            抓取结果列表
        """
        results = []
        
        # 创建内存自适应调度器 - 防止内存爆炸
        dispatcher = None
        if CRAWL4AI_AVAILABLE and 'MemoryAdaptiveDispatcher' in globals():
            dispatcher = MemoryAdaptiveDispatcher(
                memory_threshold_percent=70.0,          # 内存超过70%时暂停
                check_interval=1.0,                      # 每秒检查一次内存
                max_session_permit=max_concurrent,      # 最大并发浏览器实例数
                memory_wait_timeout=120.0,              # 超时120秒抛出错误
                rate_limiter=RateLimiter(
                    base_delay=(0.5, 1.0),               # 基础延迟0.5-1秒
                    max_delay=10.0,                      # 最大延迟10秒
                    max_retries=2                        # 最多重试2次
                ),
            )
            logger.debug(f"Using MemoryAdaptiveDispatcher with max {max_concurrent} concurrent browsers")
        
        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            # 批量抓取（带内存控制）
            if dispatcher:
                crawl_results = await crawler.arun_many(
                    urls=urls,
                    config=self.run_config,
                    dispatcher=dispatcher  # 使用内存控制
                )
            else:
                # 如果没有dispatcher，降级为顺序抓取以避免内存问题
                logger.warning("MemoryAdaptiveDispatcher not available, using sequential crawling")
                crawl_results = []
                for url in urls:
                    try:
                        result = await crawler.arun(
                            url=url,
                            config=self.run_config
                        )
                        crawl_results.append(result)
                    except Exception as e:
                        logger.error(f"Failed to crawl {url}: {e}")
                        # 创建一个失败的结果对象
                        class FailedResult:
                            success = False
                            error_message = str(e)
                            metadata = {}
                            fit_markdown = None
                            markdown = None
                        crawl_results.append(FailedResult())
            
            # 处理结果
            for i, result in enumerate(crawl_results):
                url = urls[i] if i < len(urls) else "unknown"
                
                if result.success:
                    # 截取内容长度
                    content = result.markdown.fit_markdown or result.markdown or ""
                    if len(content) > max_content_length:
                        content = content[:max_content_length] + "..."
                    
                    results.append({
                        "success": True,
                        "url": url,
                        "title": result.metadata.get("title", "No Title"),
                        "content": content,
                        "word_count": len(content.split()),
                        "fetched_at": datetime.now().isoformat()
                    })
                    
                    logger.debug(f"Successfully fetched {url}: {len(content)} chars")
                else:
                    results.append({
                        "success": False,
                        "url": url,
                        "error": result.error_message or "Unknown error"
                    })
                    
                    logger.warning(f"Failed to fetch {url}: {result.error_message}")
        
        return results
    
    def _format_results_to_xml(self, results: List[Dict[str, Any]]) -> str:
        """
        将抓取结果格式化为XML
        
        格式示例:
        <fetch_results>
          <fetch_result>
            <url>...</url>
            <title>...</title>
            <content>...</content>
            <word_count>...</word_count>
            <fetched_at>...</fetched_at>
          </fetch_result>
        </fetch_results>
        
        Args:
            results: 抓取结果列表
            
        Returns:
            XML格式的结果
        """
        if not results:
            return "<fetch_results>\n  <message>No results</message>\n</fetch_results>"
        
        xml_parts = ["<fetch_results>"]
        
        for result in results:
            if result.get("success"):
                # 成功的结果
                xml_parts.append("  <fetch_result>")
                xml_parts.append(f"    <url>{self._escape_xml(result['url'])}</url>")
                xml_parts.append(f"    <title>{self._escape_xml(result['title'])}</title>")
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


# 简化的备用抓取器（当crawl4ai不可用时）
class SimpleFetchTool(BaseTool):
    """
    简单的网页抓取工具（备用方案）
    使用aiohttp进行基础的HTML抓取
    """
    
    def __init__(self):
        super().__init__(
            name="web_fetch_simple",
            description="Simple web page fetcher (fallback)",
            permission=ToolPermission.PUBLIC
        )
    
    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="url",
                type="string",
                description="URL to fetch",
                required=True
            )
        ]
    
    @api_retry()
    async def execute(self, **params) -> ToolResult:
        """简单的HTTP GET请求"""
        import aiohttp
        from bs4 import BeautifulSoup
        
        url = params.get("url")
        if not url:
            return ToolResult(success=False, error="URL is required")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        return ToolResult(
                            success=False,
                            error=f"HTTP {response.status}"
                        )
                    
                    html = await response.text()
                    
                    # 基础HTML解析
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # 移除script和style标签
                    for script in soup(["script", "style"]):
                        script.decompose()
                    
                    # 提取文本
                    text = soup.get_text()
                    lines = (line.strip() for line in text.splitlines())
                    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                    text = ' '.join(chunk for chunk in chunks if chunk)
                    
                    # 获取标题
                    title = soup.title.string if soup.title else "No Title"
                    
                    # 限制长度
                    max_length = 5000
                    if len(text) > max_length:
                        text = text[:max_length] + "..."
                    
                    return ToolResult(
                        success=True,
                        data={
                            "url": url,
                            "title": title,
                            "content": text,
                            "length": len(text)
                        }
                    )
                    
        except Exception as e:
            logger.error(f"Simple fetch failed: {str(e)}")
            return ToolResult(success=False, error=str(e))


# 注册工具的便捷函数
def register_web_fetch_tool():
    """注册Web抓取工具"""
    from tools.registry import register_tool
    
    if CRAWL4AI_AVAILABLE:
        register_tool(WebFetchTool())
        logger.info("Registered web fetch tool (crawl4ai)")
    else:
        register_tool(SimpleFetchTool())
        logger.info("Registered web fetch tool (simple fallback)")


if __name__ == "__main__":
    # 测试代码
    async def test():
        print("\n🧪 Web抓取工具测试")
        print("="*50)
        
        if not CRAWL4AI_AVAILABLE:
            print("⚠️ crawl4ai未安装，使用简单备用方案")
            tool = SimpleFetchTool()
            
            # 测试简单抓取
            result = await tool(url="https://github.com/Neutrino1998/artifact-flow")
            if result.success:
                print(f"✅ 抓取成功")
                print(f"   标题: {result.data['title']}")
                print(f"   内容长度: {result.data['length']} 字符")
            else:
                print(f"❌ 抓取失败: {result.error}")
            return
        
        # 使用完整的crawl4ai工具
        tool = WebFetchTool()
        
        # 测试1: 单个URL
        print("\n📍 测试1: 单个URL抓取")
        test_urls = ["https://github.com/Neutrino1998/artifact-flow"]
        
        result = await tool(urls=test_urls)
        
        if result.success:
            print(f"✅ 抓取成功")
            print(f"   成功: {result.metadata['success_count']}/{result.metadata['total_urls']}")
            print("\nXML结果（前2000字符）:")
            print(result.data[:2000] + "...")
        else:
            print(f"❌ 抓取失败: {result.error}")
        
        # 测试2: 多个URL
        print("\n📍 测试2: 批量URL抓取（带并发控制）")
        test_urls = [
            "https://github.com/Neutrino1998/artifact-flow",
            "https://www.python.org",
            "https://github.com"
        ]
        
        result = await tool(
            urls=test_urls,
            max_content_length=2000,
            max_concurrent=2  # 限制并发数防止内存问题
        )
        
        if result.success:
            print(f"✅ 批量抓取完成 (并发数: 2)")
            print(f"   成功: {result.metadata['success_count']}/{result.metadata['total_urls']}")
            print(f"   失败: {result.metadata['failed_count']}")
        else:
            print(f"❌ 抓取失败: {result.error}")
    
    # 运行测试
    asyncio.run(test())