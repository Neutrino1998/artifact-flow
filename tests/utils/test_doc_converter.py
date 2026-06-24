"""
Unit tests for src/utils/doc_converter.py

上传翻转后(2026-06-11)的路由契约:**文本白名单(EXTENSION_MIME_MAP)→
content,png/jpeg → 识图 blob,其余一律 → blob**。路由纯声明式(按扩展名),
charset 启发式不参与路由判定:
- 非白名单扩展(含无扩展名/未知扩展)→ 直进 blob,不试解码、不验 magic
  (近 ASCII 二进制试解码会"成功"并丢原始字节 —— review P2;改后缀/损坏
  照收,诊断归沙盒里的模型);MIME 取 _BINARY_EXTENSION_MIME 显式表 →
  mimetypes 猜 → octet-stream
- 文本白名单:成功标对 MIME;声明文本但解不出 / 超文本帽 → blob(不再 422)
- png/jpg 扩展:Pillow 内容探测仍 loud-fail(识图路由是上传期决策,这道闸是
  路由正确性、不是格式预判)
"""

import io

import pytest
from PIL import Image

from config import config
from utils.doc_converter import (
    DocConverter,
    _BINARY_EXTENSION_MIME,
)


def _png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


# ============================================================
# 已知二进制扩展 → blob(零预判,真实 MIME)
# ============================================================


class TestKnownBinaryToBlob:
    """每个已知二进制 ext 都直进 blob:content 空、原件不变、MIME=表里的真实值。"""

    @pytest.mark.parametrize("ext", sorted(_BINARY_EXTENSION_MIME.keys()))
    async def test_each_known_binary_ext_stored_as_blob(self, ext: str):
        converter = DocConverter()
        data = b"\x00\x01\x02 arbitrary bytes, no magic expected"
        result = await converter.convert(data, f"file{ext}")
        assert result.content == ""                      # 无文本表示
        assert result.blob == data                       # 原件不变
        # XOR:blob-only,content_type 即原件真实 MIME(无独立 blob_content_type)
        assert result.content_type == _BINARY_EXTENSION_MIME[ext]
        assert result.metadata["original_filename"] == f"file{ext}"

    async def test_uppercase_extension_also_routed(self):
        """扩展名比较前会 .lower(),大小写要一致命中。"""
        converter = DocConverter()
        result = await converter.convert(b"\x00\x01\x02", "REPORT.DOC")
        assert result.blob is not None
        assert result.content_type == "application/msword"

    async def test_renamed_ole2_doc_as_docx_accepted(self):
        """改后缀的旧版 .doc(OLE2 magic)→ 照收 blob,不再 magic 拒。
        模型 mount 进沙盒后 pandoc 报错、自己诊断(翻转决策 2026-06-11)。"""
        converter = DocConverter()
        ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        result = await converter.convert(ole2, "report.docx")
        assert result.blob == ole2
        assert result.content_type.endswith("wordprocessingml.document")

    async def test_garbage_pdf_accepted_as_blob(self):
        """非 %PDF- 开头照收(同上:loud-fail at upload → loud-fail at first use)。"""
        converter = DocConverter()
        result = await converter.convert(b"not a pdf", "fake.pdf")
        assert result.blob == b"not a pdf"
        assert result.content_type == "application/pdf"

    async def test_gif_routed_to_blob_with_image_mime(self):
        """异型图入 blob、标真实 image/*(read 路径 Pillow 能解的直接可看)。"""
        converter = DocConverter()
        result = await converter.convert(b"GIF89a fake", "anim.gif")
        assert result.blob is not None
        assert result.content_type == "image/gif"

    async def test_zip_routed_to_blob(self):
        converter = DocConverter()
        result = await converter.convert(b"PK\x03\x04zipbytes", "bundle.zip")
        assert result.content_type == "application/zip"
        assert result.blob is not None


# ============================================================
# Text 分支 happy path
# ============================================================


class TestTextFallback:
    async def test_txt_returns_plain(self):
        converter = DocConverter()
        result = await converter.convert("hello world\n".encode("utf-8"), "note.txt")
        assert result.content_type == "text/plain"
        assert "hello world" in result.content
        assert result.metadata["converter_used"] == "charset-normalizer"

    async def test_md_returns_markdown(self):
        converter = DocConverter()
        result = await converter.convert("# Title\n\nBody".encode("utf-8"), "doc.md")
        assert result.content_type == "text/markdown"
        assert "# Title" in result.content

    async def test_csv_returns_csv_mime_with_raw_text(self):
        """CSV 不做结构化解析，按文本读 + 标 text/csv MIME。"""
        content = "name,age\nAlice,30\nBob,25\n"
        converter = DocConverter()
        result = await converter.convert(content.encode("utf-8"), "people.csv")
        assert result.content_type == "text/csv"
        assert result.content == content

    async def test_chinese_utf8_detected(self):
        converter = DocConverter()
        result = await converter.convert(
            "订单号,客户\nA-1,蓝湾科技\n".encode("utf-8"), "sales.csv"
        )
        assert "蓝湾科技" in result.content
        assert result.metadata["detected_encoding"].lower().startswith("utf")

    async def test_unknown_extension_goes_to_blob_even_if_texty(self):
        """路由纯声明式:非白名单扩展不试解码,纯 ASCII 内容也进 blob。
        (MIME 不钉死 —— mimetypes 对长尾扩展的认知平台相关,.xyz 在部分平台
        是化学格式 chemical/x-xyz;契约只要求"非文本、原字节保住"。)"""
        converter = DocConverter()
        result = await converter.convert(b"raw text content", "weird.xyz")
        assert result.content == ""
        assert result.blob == b"raw text content"
        assert not result.content_type.startswith("text/")

    async def test_svg_is_text_not_image(self):
        """svg 是 XML 文本:走文本路径、不标 image/*(text artifact 无 blob,
        标 image/* 会误入 read_artifact 识图分支)。"""
        converter = DocConverter()
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        result = await converter.convert(svg, "logo.svg")
        assert result.content_type == "text/xml"
        assert result.blob is None
        assert "<svg" in result.content


# ============================================================
# 路由声明式 + 文本路径兜底落 blob(翻转:不再 422)
# ============================================================


class TestDeclarativeRoutingToBlob:
    @pytest.mark.parametrize(
        "data,filename",
        [
            (b"MZ\x90\x00 this exe header is pure ascii", "tool.exe"),
            (b"plain ascii pretending to be binary", "data.bin"),
            (b"\x00\x00\x00\x18ftypmp42 tiny mp4 head", "clip.mp4"),
            (b"#!/bin/sh\necho extensionless", "Makefile"),
        ],
    )
    async def test_non_whitelist_never_decodes_to_text(self, data, filename):
        """review P2 回归:近 ASCII 的二进制(charset-normalizer 能"成功"解码)
        绝不能变成文本 artifact 丢掉原始字节 —— 非白名单一律 blob,不试解码。"""
        converter = DocConverter()
        result = await converter.convert(data, filename)
        assert result.content == ""
        assert result.blob == data  # 原始字节保住

    async def test_undecodable_whitelisted_ext_falls_to_blob(self):
        """白名单扩展但内容非文本(改后缀的二进制)→ blob octet-stream
        (按声明扩展猜 text/* 反而撒谎),沙盒里诊断。"""
        converter = DocConverter()
        data = bytes(range(256)) * 8  # 充分非文本
        result = await converter.convert(data, "mystery.txt")
        assert result.content == ""
        assert result.blob == data
        assert result.content_type == "application/octet-stream"
        assert result.metadata["converter_used"] == "blob"

    async def test_oversize_text_falls_to_blob(self, monkeypatch):
        """超 MAX_TEXT_CONVERT_BYTES 的文本类 → blob(可下载、可 mount 进沙盒
        grep/拆分),不再 422。MIME 按扩展名猜(真文本,text/plain 诚实)。"""
        monkeypatch.setattr(config, "MAX_TEXT_CONVERT_BYTES", 8)
        converter = DocConverter()
        result = await converter.convert(b"a" * 9, "notes.txt")
        assert result.content == ""
        assert result.blob == b"a" * 9
        assert result.content_type == "text/plain"  # mimetypes 按 .txt 猜
        # At/under the text cap still converts fine.
        result = await converter.convert(b"hello", "notes.txt")
        assert result.content == "hello"
        assert result.blob is None


# ============================================================
# Size limit(入口绝对上限,唯一的体积 422)
# ============================================================


class TestSizeLimit:
    async def test_oversize_raises(self, monkeypatch):
        # Shrink the limit instead of allocating a real >MAX_FILE_SIZE buffer
        # (now 100MB — building it in RAM would be wasteful). The constant tracks
        # config.MAX_UPLOAD_SIZE; the check reads the class attr, so patching it
        # exercises the same branch.
        monkeypatch.setattr(DocConverter, "MAX_FILE_SIZE", 8)
        converter = DocConverter()
        with pytest.raises(ValueError, match="too large"):
            await converter.convert(b"a" * 9, "huge.txt")

    async def test_text_cap_does_not_gate_images(self, monkeypatch):
        """An image over the *text* cap is NOT affected by it — images go through
        the blob path, not _convert_text, so the tight text cap must not apply."""
        monkeypatch.setattr(config, "MAX_TEXT_CONVERT_BYTES", 8)
        png = _png_bytes(16, 16)
        assert len(png) > 8  # comfortably over the (shrunken) text cap
        result = await DocConverter().convert(png, "shot.png")
        assert result.content_type.startswith("image/")


# ============================================================
# 图片(png/jpeg)→ blob 存储 + 内容探测 + 解压炸弹拒绝
# ============================================================


class TestImageConversion:
    """A 识图地基:有效 png/jpeg 存为不可读 blob(content 空);损坏/超像素 loud-fail。
    这道闸在翻转后保留 —— 识图路由是上传期决策(路由正确性,非格式预判)。"""

    async def test_valid_png_stored_as_blob(self):
        converter = DocConverter()
        data = _png_bytes(40, 30)
        result = await converter.convert(data, "shot.png")
        assert result.content == ""               # 图无文本表示
        assert result.content_type == "image/png"  # XOR:即原件真实 MIME
        assert result.blob == data                # 原件不变

    async def test_content_sniffed_not_extension(self):
        """真 PNG 字节改名 .jpg → 探测纠正到 image/png(按内容、非扩展名)。"""
        converter = DocConverter()
        result = await converter.convert(_png_bytes(16, 16), "mislabeled.jpg")
        assert result.content_type == "image/png"

    async def test_corrupt_image_loud_fails(self):
        converter = DocConverter()
        with pytest.raises(ValueError, match="PNG/JPEG"):
            await converter.convert(b"\x89PNG\r\n\x1a\n" + b"garbage", "broken.png")

    async def test_pixel_bomb_rejected(self, monkeypatch):
        """小文件大像素图(解压炸弹):显式 w*h 闸在解码前拒,归入 loud-fail。"""
        monkeypatch.setattr(config, "VISION_IMAGE_MAX_PIXELS", 100)  # 20x20=400px 超
        converter = DocConverter()
        with pytest.raises(ValueError, match="PNG/JPEG"):
            await converter.convert(_png_bytes(20, 20), "bomb.png")
