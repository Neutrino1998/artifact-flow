"""
Document converter for file import.

Supports:
- Import: text files (charset-normalizer), images (png/jpeg → blob),
  rich formats .docx/.pdf (→ immutable blob, NO text conversion — reading
  rich formats is a sandbox capability, see sandbox plan principle 6 / C-0)
- PDF text extraction (pymupdf) survives as a standalone helper for
  web_fetch's PDF fallback — that is web-content reading, not upload.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from config import config
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

# Office / ODF 二进制 + 模板 + 宏文件 + 演示/表格的 OOXML：全都落 charset-normalizer
# 兜底要么抛 "Cannot decode" 要么解出乱码。在 convert() 入口按扩展名早返回，并按
# 文件类型给出针对性 remediation —— 让用户"把 Excel 另存为 docx"是没意义的。
# 不走 magic-byte：OOXML 各家都是 PK\x03\x04（zip），要区分需要解压看
# [Content_Types].xml，复杂度划不来。
_Cat = Tuple[str, str]  # (category, remediation_advice)

_WORD_TO_DOCX: _Cat = ("Word", "请用 Office/WPS 另存为 .docx 后再上传")
_WORD_MACRO_TO_DOCX: _Cat = ("Word", "请用 Office/WPS 另存为 .docx（取消宏）后再上传")
_EXCEL_TO_CSV: _Cat = ("Excel", "请导出为 .csv，或将需要的内容复制到对话框")
_PPT_TO_PDF: _Cat = ("PowerPoint", "请导出为 PDF（文字版），或将需要的内容复制到对话框")
_ODF_TEXT: _Cat = ("ODF 文档", "请另存为 .docx 或 .pdf 后再上传")
_ODF_CALC: _Cat = ("ODF 表格", "请导出为 .csv，或将需要的内容复制到对话框")
_ODF_IMPRESS: _Cat = ("ODF 演示", "请导出为 PDF（文字版），或将需要的内容复制到对话框")

# 图片:识图路径只认 png/jpeg(见 sandbox plan A 决策)。真实 MIME 由 Pillow 按内容
# 探测、非按扩展名,故这里只用扩展名圈定"走图片分支"。其它图片格式存为不可读 blob 无
# 意义,上传即拒 + 转换建议(仿 _UNSUPPORTED_OFFICE 的 idiom)。
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_UNSUPPORTED_IMAGE: Dict[str, str] = {
    ext: "请另存为 PNG 或 JPG 后再上传"
    for ext in (".gif", ".webp", ".bmp", ".tiff", ".tif",
                ".heic", ".heif", ".svg", ".ico", ".avif")
}

# 富格式原始 blob 的真实 MIME(blob-only 存储:artifact 无文本表示,content_type
# 即原件 MIME → raw 端点按它发,读/转换归沙盒)。
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"

_UNSUPPORTED_OFFICE: Dict[str, _Cat] = {
    # Word（老二进制 + 模板 + 宏）
    ".doc": _WORD_TO_DOCX,
    ".docm": _WORD_MACRO_TO_DOCX,
    ".docb": _WORD_TO_DOCX,
    ".dot": _WORD_TO_DOCX,
    ".dotx": _WORD_TO_DOCX,
    ".dotm": _WORD_MACRO_TO_DOCX,
    # Excel（老二进制 + 现代 OOXML + 模板 + 宏 + 二进制工作簿）
    ".xls": _EXCEL_TO_CSV,
    ".xlsx": _EXCEL_TO_CSV,
    ".xlsm": _EXCEL_TO_CSV,
    ".xlsb": _EXCEL_TO_CSV,
    ".xlt": _EXCEL_TO_CSV,
    ".xltx": _EXCEL_TO_CSV,
    ".xltm": _EXCEL_TO_CSV,
    # PowerPoint（老二进制 + 现代 + 模板 + 宏 + 自动播放）
    ".ppt": _PPT_TO_PDF,
    ".pptx": _PPT_TO_PDF,
    ".pptm": _PPT_TO_PDF,
    ".pps": _PPT_TO_PDF,
    ".ppsx": _PPT_TO_PDF,
    ".ppsm": _PPT_TO_PDF,
    ".pot": _PPT_TO_PDF,
    ".potx": _PPT_TO_PDF,
    ".potm": _PPT_TO_PDF,
    # LibreOffice / ODF
    ".odt": _ODF_TEXT,
    ".ott": _ODF_TEXT,
    ".ods": _ODF_CALC,
    ".ots": _ODF_CALC,
    ".odp": _ODF_IMPRESS,
    ".otp": _ODF_IMPRESS,
}


@dataclass
class ConvertResult:
    """Result of a document conversion.

    `content`/`content_type` 是**可读文本表示**(纯文本即原文;图片与富格式
    docx/pdf 为空 —— 无文本表示,content_type 即真实 MIME)。`blob`/
    `blob_content_type` 是需要**二进制存储**时的原始不可变字节 + 真实 MIME;
    纯文本类无 blob。富格式的读/写/转换全归沙盒(sandbox plan 原则 6,C-0
    起 blob-only,不再预转 md)。
    """
    content: str
    content_type: str  # MIME type of the readable text representation
    metadata: Dict = field(default_factory=dict)
    blob: Optional[bytes] = None              # 原始字节(需 blob 存储时;纯文本为 None)
    blob_content_type: Optional[str] = None   # 原始 blob 的真实 MIME


class DocConverter:
    """
    Unified document converter for import (file -> text or blob).
    """

    # Absolute convert() backstop = config.MAX_UPLOAD_SIZE (the ingress ceiling).
    # The upload path (artifacts.py) enforces its own authoritative limit BEFORE
    # we see the bytes, so this only fires if a caller forgot to; tying it to
    # the upload constant avoids two hardcoded values drifting apart. NOTE: this
    # is NOT the per-path cost guard. Each path owns its own: docx/pdf store the
    # blob raw (magic-byte check only, no parsing), images via the pixel cap +
    # raw-blob store (no text), and the raw-text path via MAX_TEXT_CONVERT_BYTES
    # (it's the only one that materializes full content+wordlist, so it keeps a
    # tighter cap than this one).
    MAX_FILE_SIZE = config.MAX_UPLOAD_SIZE
    MAX_PDF_PAGES = 200                # extract_pdf_text (web_fetch fallback) only

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
        elif ext in _IMAGE_EXTENSIONS:
            return await self._convert_image(file_bytes, filename)
        elif ext in _UNSUPPORTED_IMAGE:
            raise ValueError(
                f"暂不支持 {ext} 图片格式。{_UNSUPPORTED_IMAGE[ext]}。"
            )
        elif ext in _UNSUPPORTED_OFFICE:
            category, advice = _UNSUPPORTED_OFFICE[ext]
            raise ValueError(
                f"暂不支持 {ext} 格式（{category} 文件）。{advice}。"
            )
        else:
            return await self._convert_text(file_bytes, filename, ext)

    async def extract_pdf_text(self, file_bytes: bytes) -> Tuple[str, int]:
        """Extract text from PDF bytes via pymupdf. Returns (text, page_count).

        web_fetch 的 PDF 降级路径专用(网页内容阅读,非上传)。上传的 .pdf 走
        blob-only(`_convert_pdf`),不经此函数 —— 富格式的读归沙盒(原则 6)。
        """
        # PyMuPDF 必须串行（见模块顶部 _PYMUPDF_EXECUTOR 注释），同时不卡 event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _PYMUPDF_EXECUTOR, _extract_pdf_text, file_bytes, self.MAX_PDF_PAGES
        )

    async def _convert_docx(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """.docx → 不可变 blob(无文本转换;读 docx 是沙盒能力,C-0 起 blob-only)。"""
        # zip 预检:.docx 是 OOXML(zip 容器),合法文件以 PK\x03\x04 开头。改后缀的
        # .doc 是 OLE2(D0CF11E0)——存成 blob 后模型在沙盒里也打不开,上传即拒 +
        # 可操作提示。注意:这不违反模块顶部「不走 magic-byte 区分 OOXML *种类*」——
        # 区分 docx/xlsx/pptx 才需解压看 [Content_Types].xml;这里只判容器是不是
        # zip,代价极低。
        if not file_bytes.startswith(b"PK\x03\x04"):
            raise ValueError(
                f"{filename!r} 不是有效的 .docx 文件(可能是改了后缀的旧版 .doc 或损坏文件)。"
                "请用 Word 另存为 .docx 后重新上传。"
            )
        return ConvertResult(
            content="",
            content_type=_DOCX_MIME,
            metadata={"original_filename": filename, "converter_used": "blob"},
            # 不可变源 + 未来沙盒 pandoc --reference-doc 样式模版,一物两用
            blob=file_bytes,
            blob_content_type=_DOCX_MIME,
        )

    async def _convert_pdf(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """.pdf → 不可变 blob(无文本转换;读 pdf 是沙盒能力,C-0 起 blob-only)。"""
        # magic 预检(对齐 docx 的 idiom):合法 PDF 以 %PDF- 开头,改后缀/损坏文件
        # 存成 blob 后沙盒里也解析不了,上传即拒。
        if not file_bytes.startswith(b"%PDF-"):
            raise ValueError(
                f"{filename!r} 不是有效的 .pdf 文件(可能改了后缀或已损坏)。"
                "请确认文件可正常打开后重新上传。"
            )
        return ConvertResult(
            content="",
            content_type=_PDF_MIME,
            metadata={"original_filename": filename, "converter_used": "blob"},
            blob=file_bytes,
            blob_content_type=_PDF_MIME,
        )

    async def _convert_image(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """图片(png/jpeg)→ blob 存储。

        真实格式由 Pillow **按内容**探测(非按扩展名),改后缀/伪装的图也能纠正到
        正确 MIME;探测顺带挡损坏、截断、解压炸弹(显式 w*h ≤ VISION_IMAGE_MAX_PIXELS)。`content`
        留空 —— 图无文本表示,模型靠 read_artifact 取图块(A-vision)。非 png/jpeg
        的真实格式(探测出 GIF/WEBP 等)同样 loud-fail。
        """
        loop = asyncio.get_running_loop()
        fmt = await loop.run_in_executor(None, _probe_image, file_bytes)
        if fmt == "PNG":
            mime = "image/png"
        elif fmt == "JPEG":
            mime = "image/jpeg"
        else:
            raise ValueError(
                f"{filename!r} 不是有效的 PNG/JPEG 图片(可能改了后缀、损坏、超大像素、"
                "或实为其它图片格式)。请另存为 PNG 或 JPG 后重新上传。"
            )
        return ConvertResult(
            content="",
            content_type=mime,
            metadata={"original_filename": filename, "converter_used": "pillow"},
            blob=file_bytes,
            blob_content_type=mime,
        )

    async def _convert_text(
        self, file_bytes: bytes, filename: str, ext: str
    ) -> ConvertResult:
        """
        Try to read file as text with charset detection.
        Raises ValueError if the file cannot be decoded or exceeds the text cap.
        """
        # The text path is the one conversion route with NO cost envelope of its
        # own: charset detection + str() + word-count materialize the full decoded
        # content AND a word list, which amplifies memory well past the input size.
        # docx/pdf store the blob raw (magic check only), images store the
        # blob raw (no text) — only raw text scales with the 100MB upload ceiling.
        # So it keeps a tighter, independent cap (MAX_TEXT_CONVERT_BYTES, the old
        # 20MB envelope). The byte cap is the PRIMARY guard (an input upper bound);
        # to_thread below is the secondary one (keeps the loop responsive +
        # cancellable during the bounded sync work — it does NOT bound memory).
        if len(file_bytes) > config.MAX_TEXT_CONVERT_BYTES:
            cap_mb = config.MAX_TEXT_CONVERT_BYTES / 1024 / 1024
            raise ValueError(
                f"Text file too large: {len(file_bytes) / 1024 / 1024:.1f}MB "
                f"(max {cap_mb:.0f}MB for text; images / PDF / docx may be larger)"
            )

        def _decode() -> tuple[str, str, int]:
            """Sync decode + word count, run in a worker thread so the loop stays
            responsive and the work is cancellable. All the materialization (the
            decoded str + the split() word list) happens here, off the loop."""
            from charset_normalizer import from_bytes

            best = from_bytes(file_bytes).best()
            if best is None:
                raise ValueError(
                    f"Cannot decode file '{filename}': not a valid text file"
                )
            text = str(best)
            return text, best.encoding, len(text.split())

        content, detected_encoding, word_count = await asyncio.to_thread(_decode)
        content_type = EXTENSION_MIME_MAP.get(ext, "text/plain")

        return ConvertResult(
            content=content,
            content_type=content_type,
            metadata={
                "original_filename": filename,
                "converter_used": "charset-normalizer",
                "detected_encoding": detected_encoding,
                "word_count": word_count,
            },
        )


def _probe_image(file_bytes: bytes) -> Optional[str]:
    """同步 Pillow 探测:返回真实图片格式（'PNG' / 'JPEG' / ...），非法/损坏/超像素返回 None。

    跑在 executor 里（CPU 纪律:校验可能解码、且 Pillow 是 C 扩展）。解压炸弹闸不依赖
    Pillow 默认的 `MAX_IMAGE_PIXELS`(89–178M 段只 warn 不抛,会漏过 100M 像素的小文件炸弹),
    而是在 **解码前** 用 `img.size`(open 只读头、不解码)显式校验 `w*h ≤ VISION_IMAGE_MAX_PIXELS`,
    超限即 None(loud-fail 给可操作提示)。verify() 做结构校验（截断/损坏即抛）;它会使 image
    对象失效,故先取 format/size 再 verify。
    """
    import io

    from PIL import Image

    from config import config

    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            fmt = img.format
            w, h = img.size
            if w * h > config.VISION_IMAGE_MAX_PIXELS:
                return None
            img.verify()
        return fmt
    except Exception:
        return None


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
