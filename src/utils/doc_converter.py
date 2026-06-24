"""
Document converter for file import.

路由(上传翻转后,2026-06-11):**文本白名单(EXTENSION_MIME_MAP)→ content,
png/jpeg → 识图 blob,其余一律 → blob**。路由纯声明式(按扩展名三分),
charset 启发式不参与路由判定 —— 改后缀 / 损坏 / 不认识的字节照收进 blob,
模型 mount 进沙盒后自己检视、诊断、转换(remediation 提示归 skill 系统);
"loud-fail at upload" 随沙盒落地降级为 "loud-fail at first use",不再需要
magic 闸与拒绝名单。仅存的上传期 ValueError:体积超限、png/jpg 扩展名但
Pillow 探不出合法 PNG/JPEG(识图路由是上传期决策,这道闸是路由正确性、
不是格式预判)。

- PDF text extraction (pymupdf) survives as a standalone helper for
  web_fetch's PDF fallback — that is web-content reading, not upload.
"""

import asyncio
import mimetypes
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
    # svg 是 XML 文本:走文本路径(可读可编辑),不标 image/*(text artifact 无 blob,
    # 标 image/* 会误入 read_artifact 识图分支)。沙盒里它也是按文本处理的格式。
    ".svg": "text/xml",
}

# 图片(png/jpeg):识图路径(见 sandbox plan A 决策)。真实 MIME 由 Pillow 按内容
# 探测、非按扩展名,故这里只用扩展名圈定"走图片分支"。
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# 富格式原始 blob 的真实 MIME(blob-only 存储:artifact 无文本表示,content_type
# 即原件 MIME → raw 端点按它发,读/转换归沙盒)。
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"

# 已知二进制扩展名 → 真实 MIME:**不是接受闸也不是路由表**(路由 = 文本白名单
# 之外一律 blob,见 convert()),只管 MIME 正确性 —— mimetypes 对 OOXML/heic/avif
# 等的认知因平台而异,显式表保证 /raw 端点按真实 MIME 发(浏览器下载/内联行为
# 正确)。不在表里的未知扩展由 mimetypes 猜、兜底 octet-stream。
_BINARY_EXTENSION_MIME: Dict[str, str] = {
    # Word
    ".docx": _DOCX_MIME,
    ".doc": "application/msword",
    ".docb": "application/msword",
    ".dot": "application/msword",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".dotm": "application/vnd.ms-word.template.macroEnabled.12",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    # PDF
    ".pdf": _PDF_MIME,
    # Excel
    ".xls": "application/vnd.ms-excel",
    ".xlt": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    ".xltm": "application/vnd.ms-excel.template.macroEnabled.12",
    # PowerPoint
    ".ppt": "application/vnd.ms-powerpoint",
    ".pps": "application/vnd.ms-powerpoint",
    ".pot": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    ".potx": "application/vnd.openxmlformats-officedocument.presentationml.template",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ".ppsm": "application/vnd.ms-powerpoint.slideshow.macroEnabled.12",
    ".potm": "application/vnd.ms-powerpoint.template.macroEnabled.12",
    # LibreOffice / ODF
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ott": "application/vnd.oasis.opendocument.text-template",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".ots": "application/vnd.oasis.opendocument.spreadsheet-template",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".otp": "application/vnd.oasis.opendocument.presentation-template",
    # 非 png/jpeg 图片:照收 blob、标真实 image/* MIME。read_artifact 识图分支会
    # 直接尝试(Pillow 能解 gif/webp/bmp/tiff → 降采样重编码后照样可看;heic/avif
    # 等解不了 → loud-fail + 提示 mount 进沙盒转换)。
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    # 压缩包(原则 5:zip 等进 blob,沙盒里解)
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
    ".tar": "application/x-tar",
    ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
}


@dataclass
class ConvertResult:
    """Result of a document conversion.

    XOR:一个结果只一份实质 data —— 纯文本即 `content`(`content_type` 为 text MIME,
    `blob=None`);图片与富格式 docx/pdf 为 `blob`(`content=""`,`content_type` 即原件
    真实 MIME,无需另给 blob MIME)。富格式的读/写/转换全归沙盒(sandbox plan 原则 6,
    C-0 起 blob-only,不再预转 md)。
    """
    content: str
    content_type: str  # 文本表示的 MIME;blob-only 时即原件真实 MIME
    metadata: Dict = field(default_factory=dict)
    blob: Optional[bytes] = None              # 原始字节(需 blob 存储时;纯文本为 None)


class DocConverter:
    """
    Unified document converter for import (file -> text or blob).
    """

    # Absolute convert() backstop = config.MAX_UPLOAD_SIZE (the ingress ceiling).
    # The upload path (artifacts.py) enforces its own authoritative limit BEFORE
    # we see the bytes, so this only fires if a caller forgot to; tying it to
    # the upload constant avoids two hardcoded values drifting apart. NOTE: this
    # is NOT the per-path cost guard. Each path owns its own: blob routes store
    # the bytes raw (no parsing), images via the pixel cap + raw-blob store (no
    # text), and the raw-text path via MAX_TEXT_CONVERT_BYTES (it's the only one
    # that materializes full content+wordlist, so it keeps a tighter cap — over
    # it the file falls to blob, not 422).
    MAX_FILE_SIZE = config.MAX_UPLOAD_SIZE
    MAX_PDF_PAGES = 200                # extract_pdf_text (web_fetch fallback) only

    async def convert(self, file_bytes: bytes, filename: str) -> ConvertResult:
        """
        Convert a file to text or blob (import).

        Args:
            file_bytes: Raw file bytes
            filename: Original filename (used for extension detection)

        Returns:
            ConvertResult — text content, or blob with the original bytes

        Raises:
            ValueError: file too large, or png/jpg extension that Pillow
                cannot probe as valid PNG/JPEG (vision-routing gate)
        """
        if len(file_bytes) > self.MAX_FILE_SIZE:
            raise ValueError(
                f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB "
                f"(max {self.MAX_FILE_SIZE / 1024 / 1024:.0f}MB)"
            )

        ext = os.path.splitext(filename)[1].lower()

        if ext in _IMAGE_EXTENSIONS:
            return await self._convert_image(file_bytes, filename)
        elif ext in EXTENSION_MIME_MAP:
            # 文本白名单:唯一进解码的路由(白名单内解不出/超帽仍落 blob)。
            return await self._convert_text(file_bytes, filename, ext)
        else:
            # 非白名单一律 blob —— 路由纯声明式(按扩展名),charset 启发式
            # 不参与路由判定:近 ASCII 的二进制(.exe 头/小 mp4/ascii .bin)能被
            # charset-normalizer "成功"解码,试一下就会把原始字节丢成文本
            # artifact(不可下载/不可 mount)。无扩展名(README/Makefile)同落
            # blob,契约一致;不验 magic,改后缀/损坏照收,模型 mount 进沙盒
            # 后自己诊断(见模块 docstring)。
            mime = _BINARY_EXTENSION_MIME.get(ext) or _guess_blob_mime(filename)
            return _blob_result(file_bytes, filename, mime)

    async def extract_pdf_text(self, file_bytes: bytes) -> Tuple[str, int]:
        """Extract text from PDF bytes via pymupdf. Returns (text, page_count).

        web_fetch 的 PDF 降级路径专用(网页内容阅读,非上传)。上传的 .pdf 走
        blob-only(已知二进制路由),不经此函数 —— 富格式的读归沙盒(原则 6)。
        """
        # PyMuPDF 必须串行（见模块顶部 _PYMUPDF_EXECUTOR 注释），同时不卡 event loop
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _PYMUPDF_EXECUTOR, _extract_pdf_text, file_bytes, self.MAX_PDF_PAGES
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
        )

    async def _convert_text(
        self, file_bytes: bytes, filename: str, ext: str
    ) -> ConvertResult:
        """
        Try to read file as text with charset detection.
        解不出文本 / 超文本帽 → 落 blob(翻转后不再拒:沙盒里模型自己处理)。
        """
        # The text path is the one conversion route with NO cost envelope of its
        # own: charset detection + str() + word-count materialize the full decoded
        # content AND a word list, which amplifies memory well past the input size.
        # Blob routes store bytes raw — only raw text scales with the 100MB upload
        # ceiling. So it keeps a tighter, independent cap (MAX_TEXT_CONVERT_BYTES);
        # over the cap → blob(可下载、可 mount 进沙盒 grep/拆分),不再 422。
        # The byte cap is the PRIMARY guard (an input upper bound); to_thread below
        # is the secondary one (keeps the loop responsive + cancellable during the
        # bounded sync work — it does NOT bound memory).
        if len(file_bytes) > config.MAX_TEXT_CONVERT_BYTES:
            logger.info(
                f"Upload '{filename}' exceeds text cap "
                f"({len(file_bytes)} > {config.MAX_TEXT_CONVERT_BYTES}), storing as blob"
            )
            return _blob_result(file_bytes, filename, _guess_blob_mime(filename))

        def _decode() -> Optional[tuple]:
            """Sync decode + word count, run in a worker thread so the loop stays
            responsive and the work is cancellable. All the materialization (the
            decoded str + the split() word list) happens here, off the loop.
            Returns None when the bytes are not text(charset 探测失败)。"""
            from charset_normalizer import from_bytes

            best = from_bytes(file_bytes).best()
            if best is None:
                return None
            text = str(best)
            return text, best.encoding, len(text.split())

        decoded = await asyncio.to_thread(_decode)
        if decoded is None:
            # 白名单扩展但解不出文本(声明是文本、内容不是,如改后缀的二进制):
            # 落 blob(octet-stream —— 按声明扩展猜 text/* 反而撒谎),沙盒里诊断。
            logger.info(f"Upload '{filename}' is not decodable text, storing as blob")
            return _blob_result(file_bytes, filename, "application/octet-stream")

        content, detected_encoding, word_count = decoded
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


def _guess_blob_mime(filename: str) -> str:
    """未知二进制的 MIME:标准库按扩展名猜,猜不出 octet-stream。"""
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _blob_result(file_bytes: bytes, filename: str, mime: str) -> ConvertResult:
    """blob-only ConvertResult:content 空(无文本表示)、content_type=真实 MIME、
    原件原样进 blob(不可变源;docx 兼未来 pandoc --reference-doc 样式模版)。"""
    return ConvertResult(
        content="",
        content_type=mime,
        metadata={"original_filename": filename, "converter_used": "blob"},
        blob=file_bytes,
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
