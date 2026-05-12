"""
Document converter for file import/export.

Supports:
- Import: .docx (pandoc), .pdf (pymupdf), text files (charset-normalizer)
- Export: markdown -> .docx (pandoc)
"""

import asyncio
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

# PyMuPDF 在 import 时调 mupdf.reinit_singlethreaded()，底层是单线程模式 ——
# 多线程并发调用会得到错误结果或直接段错误。用专属 single-worker executor
# 把 PDF 解析序列化掉，event loop 仍然不卡（其他请求继续跑），但 PyMuPDF 调用
# 永远在同一固定线程上执行，符合上游约束。详见：
# https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html
_PYMUPDF_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pymupdf")

# Extension -> MIME type mapping
EXTENSION_MIME_MAP: Dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/x-typescript",
    ".jsx": "text/javascript",
    ".tsx": "text/x-typescript",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".xml": "text/xml",
    ".csv": "text/csv",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".sql": "text/x-sql",
    ".r": "text/x-r",
    ".rb": "text/x-ruby",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".swift": "text/x-swift",
    ".kt": "text/x-kotlin",
    ".scala": "text/x-scala",
    ".lua": "text/x-lua",
    ".toml": "text/x-toml",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".log": "text/plain",
    ".env": "text/plain",
}

# 老版 Office 二进制 + 现代非 Word 的 Office 格式：落到 charset-normalizer 兜底要么
# 抛 "Cannot decode" 要么解出乱码，体验都差。统一在 convert() 入口早返回一条
# 明确的错误信息，引导用户另存为 .docx 或复制粘贴。
_UNSUPPORTED_OFFICE_EXTS = frozenset({".doc", ".ppt", ".pptx", ".xls", ".xlsx"})


@dataclass
class ConvertResult:
    """Result of a document conversion."""
    content: str
    content_type: str  # MIME type
    metadata: Dict = field(default_factory=dict)


class DocConverter:
    """
    Unified document converter for import (file -> text) and export (markdown -> docx).
    """

    MAX_FILE_SIZE = 20 * 1024 * 1024   # 20MB
    MAX_PDF_PAGES = 200
    CONVERT_TIMEOUT = 60               # seconds

    @classmethod
    def check_pandoc(cls) -> None:
        """Check pandoc availability at startup. Raises RuntimeError if not found."""
        if not shutil.which("pandoc"):
            raise RuntimeError(
                "pandoc is not installed or not in PATH. "
                "Install it with: brew install pandoc (macOS) or apt-get install pandoc (Linux)"
            )
        logger.info("pandoc check passed")


    async def convert(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """
        Convert a file to text (import).

        Args:
            file_bytes: Raw file bytes
            filename: Original filename (used for extension detection)

        Returns:
            ConvertResult with text content and MIME type

        Raises:
            ValueError: File too large, too many pages, or not decodable
        """
        if len(file_bytes) > self.MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB "
                f"(max {self.MAX_FILE_SIZE / 1024 / 1024:.0f}MB)"
            )

        ext = os.path.splitext(filename)[1].lower()

        if ext == ".docx":
            return await self._convert_docx(file_bytes, filename)
        elif ext == ".pdf":
            return await self._convert_pdf(file_bytes, filename)
        elif ext in _UNSUPPORTED_OFFICE_EXTS:
            raise ValueError(
                f"暂不支持 {ext} 格式。请用 Office/WPS 另存为 .docx 后再上传，"
                f"或将内容复制粘贴到对话框。"
            )
        else:
            return await self._convert_text(file_bytes, filename, ext)

    async def export_docx(self, markdown_content: str) -> bytes:
        """
        Export markdown content to docx bytes.

        Args:
            markdown_content: Markdown text

        Returns:
            docx file bytes

        Raises:
            RuntimeError: pandoc conversion failed
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "pandoc", "-f", "markdown", "-t", "docx", "-o", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=markdown_content.encode("utf-8")),
                timeout=self.CONVERT_TIMEOUT,
            )

            if proc.returncode != 0:
                raise RuntimeError(f"pandoc export failed: {stderr.decode()}")

            return stdout

        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"pandoc export timed out ({self.CONVERT_TIMEOUT}s)")

    async def _convert_docx(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """Convert .docx to markdown via pandoc."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pandoc", "-f", "docx", "-t", "markdown",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=file_bytes),
                timeout=self.CONVERT_TIMEOUT,
            )

            if proc.returncode != 0:
                raise RuntimeError(f"pandoc conversion failed: {stderr.decode()}")

            content = stdout.decode("utf-8")
            word_count = len(content.split())

            return ConvertResult(
                content=content,
                content_type="text/markdown",
                metadata={
                    "original_filename": filename,
                    "converter_used": "pandoc",
                    "word_count": word_count,
                },
            )

        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"pandoc conversion timed out ({self.CONVERT_TIMEOUT}s)")

    async def _convert_pdf(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """Convert .pdf to markdown via pymupdf."""
        # PyMuPDF 必须串行（见模块顶部 _PYMUPDF_EXECUTOR 注释），同时不卡 event loop
        loop = asyncio.get_running_loop()
        content, page_count = await loop.run_in_executor(
            _PYMUPDF_EXECUTOR, _extract_pdf_text, file_bytes, self.MAX_PDF_PAGES
        )
        word_count = len(content.split())

        return ConvertResult(
            content=content,
            content_type="text/markdown",
            metadata={
                "original_filename": filename,
                "converter_used": "pymupdf",
                "page_count": page_count,
                "word_count": word_count,
            },
        )

    async def _convert_text(
        self, file_bytes: bytes, filename: str, ext: str
    ) -> ConvertResult:
        """
        Try to read file as text with charset detection.
        Raises ValueError if the file cannot be decoded.
        """
        from charset_normalizer import from_bytes

        result = from_bytes(file_bytes)
        best = result.best()

        if best is None:
            raise ValueError(
                f"Cannot decode file '{filename}': not a valid text file"
            )

        content = str(best)
        content_type = EXTENSION_MIME_MAP.get(ext, "text/plain")
        word_count = len(content.split())

        return ConvertResult(
            content=content,
            content_type=content_type,
            metadata={
                "original_filename": filename,
                "converter_used": "charset-normalizer",
                "detected_encoding": best.encoding,
                "word_count": word_count,
            },
        )


def _extract_pdf_text(file_bytes: bytes, max_pages: int) -> tuple[str, int]:
    """Sync pymupdf extraction. Designed to run inside asyncio.to_thread."""
    import pymupdf

    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    try:
        page_count = len(doc)
        if page_count > max_pages:
            raise ValueError(f"PDF has {page_count} pages (max {max_pages})")

        text_parts = []
        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(f"## Page {page_num + 1}\n\n{text.strip()}")

        return "\n\n".join(text_parts), page_count
    finally:
        doc.close()
