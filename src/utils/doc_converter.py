"""
Document converter for file import/export.

Supports:
- Import: .docx (pandoc), .pdf (pymupdf), text files (charset-normalizer)
- Export: markdown -> .docx (pandoc)
"""

import asyncio
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

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

    @classmethod
    def check_pymupdf(cls) -> None:
        """Check pymupdf availability at startup. Raises RuntimeError if not found."""
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "pymupdf is not installed. Install with: pip install pymupdf"
            )
        logger.info("pymupdf check passed")

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
        import pymupdf

        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)

        if page_count > self.MAX_PDF_PAGES:
            doc.close()
            raise ValueError(
                f"PDF has {page_count} pages (max {self.MAX_PDF_PAGES})"
            )

        text_parts = []
        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(f"## Page {page_num + 1}\n\n{text.strip()}")

        doc.close()

        content = "\n\n".join(text_parts)
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
