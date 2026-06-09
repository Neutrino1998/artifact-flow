"""
Unit tests for src/utils/doc_converter.py

覆盖 import 流程的扩展名分支：
- Unsupported Office / ODF：每个扩展名都抛 ValueError，文案带分类 + 针对性 advice
- Text fallback：纯文本扩展（.txt/.md/.csv）成功并标对 MIME 类型
- 未知扩展名：能解码为文本 → 走兜底；不能解码 → ValueError

不覆盖 .docx / .pdf 的 happy path —— 需要真实 pandoc / pymupdf 二进制做 fixture，
属于 integration 测试范畴，不在这里。
"""

import io

import pytest
from PIL import Image

from config import config
from utils.doc_converter import (
    DocConverter,
    _UNSUPPORTED_OFFICE,
)


def _png_bytes(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


# ============================================================
# Unsupported Office / ODF 拒绝
# ============================================================


class TestUnsupportedOfficeRejection:
    """每个不支持的 ext 都要早返回 + 文案带分类 + advice。"""

    @pytest.mark.parametrize("ext", sorted(_UNSUPPORTED_OFFICE.keys()))
    async def test_each_unsupported_ext_raises_value_error(self, ext: str):
        converter = DocConverter()
        # 文件内容随便给 —— 入口按扩展名拦截，根本不会到解码这一步
        with pytest.raises(ValueError) as exc_info:
            await converter.convert(b"irrelevant binary blob", f"file{ext}")

        msg = str(exc_info.value)
        # 文案必须带 ext 本身（让用户知道什么文件被拒）
        assert ext in msg
        # 必须带分类（Word / Excel / PowerPoint / ODF *）
        category, advice = _UNSUPPORTED_OFFICE[ext]
        assert category in msg
        # 必须带 advice（remediation 建议）
        assert advice in msg

    async def test_uppercase_extension_also_rejected(self):
        """扩展名比较前会 .lower()，大小写要一致命中。"""
        converter = DocConverter()
        with pytest.raises(ValueError, match="Word"):
            await converter.convert(b"\x00\x01\x02", "REPORT.DOC")

    async def test_excel_advice_mentions_csv(self):
        converter = DocConverter()
        with pytest.raises(ValueError) as exc:
            await converter.convert(b"\x00", "data.xlsx")
        assert ".csv" in str(exc.value)

    async def test_powerpoint_advice_mentions_pdf(self):
        converter = DocConverter()
        with pytest.raises(ValueError) as exc:
            await converter.convert(b"\x00", "slides.pptx")
        assert "PDF" in str(exc.value)

    async def test_docm_advice_mentions_macro(self):
        """带宏的 Word 文件 advice 要提示取消宏，避免用户存出又一个 .docm。"""
        converter = DocConverter()
        with pytest.raises(ValueError) as exc:
            await converter.convert(b"\x00", "macros.docm")
        assert "宏" in str(exc.value)


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

    async def test_unknown_extension_falls_to_plain(self):
        converter = DocConverter()
        result = await converter.convert(b"raw text content", "weird.xyz")
        assert result.content_type == "text/plain"
        assert "raw text content" in result.content


# ============================================================
# Size limit
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


# ============================================================
# .docx zip 预检（改后缀的 .doc → 可操作 422，而非晦涩 500）
# ============================================================


class TestDocxZipPrecheck:
    """_convert_docx 入口判「是不是合法 zip」：非 PK\\x03\\x04 → ValueError。"""

    async def test_ole2_doc_renamed_to_docx_raises_value_error(self):
        # 旧版 .doc 是 OLE2 复合文档（magic D0CF11E0），改后缀成 .docx 上传。
        # 旧行为:pandoc 抛 "couldn't unpack docx container" RuntimeError → 500。
        # 新行为:入口预检抛 ValueError（路由映射 422 + 可操作提示）。
        converter = DocConverter()
        ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
        with pytest.raises(ValueError, match="不是有效的 .docx"):
            await converter._convert_docx(ole2, "report.docx")

    async def test_garbage_bytes_raise_value_error(self):
        converter = DocConverter()
        with pytest.raises(ValueError, match="不是有效的 .docx"):
            await converter._convert_docx(b"not a zip at all", "x.docx")

    async def test_valid_zip_header_passes_precheck(self):
        # PK\x03\x04 开头通过预检（后续 pandoc 阶段可能因内容/缺二进制再失败，
        # 但绝不能是「不是有效的 .docx」这条 ValueError）。
        converter = DocConverter()
        zipped = b"PK\x03\x04" + b"\x00" * 64
        with pytest.raises(Exception) as exc_info:
            await converter._convert_docx(zipped, "ok.docx")
        assert "不是有效的 .docx" not in str(exc_info.value)


# ============================================================
# 图片(png/jpeg)→ blob 存储 + 内容探测 + 解压炸弹拒绝
# ============================================================


class TestImageConversion:
    """A 识图地基:有效 png/jpeg 存为不可读 blob(content 空);损坏/超像素 loud-fail。"""

    async def test_valid_png_stored_as_blob(self):
        converter = DocConverter()
        data = _png_bytes(40, 30)
        result = await converter.convert(data, "shot.png")
        assert result.content == ""               # 图无文本表示
        assert result.content_type == "image/png"
        assert result.blob == data                # 原件不变
        assert result.blob_content_type == "image/png"

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
