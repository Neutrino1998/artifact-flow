"""
Unit tests for src/utils/doc_converter.py

覆盖 import 流程的扩展名分支：
- Unsupported Office / ODF：每个扩展名都抛 ValueError，文案带分类 + 针对性 advice
- Text fallback：纯文本扩展（.txt/.md/.csv）成功并标对 MIME 类型
- 未知扩展名：能解码为文本 → 走兜底；不能解码 → ValueError

不覆盖 .docx / .pdf 的 happy path —— 需要真实 pandoc / pymupdf 二进制做 fixture，
属于 integration 测试范畴，不在这里。
"""

import pytest

from utils.doc_converter import (
    DocConverter,
    _UNSUPPORTED_OFFICE,
)


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
    async def test_oversize_raises(self):
        converter = DocConverter()
        oversize = b"a" * (DocConverter.MAX_FILE_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            await converter.convert(oversize, "huge.txt")
