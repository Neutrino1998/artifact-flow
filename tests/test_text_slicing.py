"""
Tests for src/utils/text_slicing.py
"""

import pytest

from utils.text_slicing import count_lines, slice_lines_by_offset_limit


# ============================================================
# count_lines
# ============================================================

class TestCountLines:
    def test_empty(self):
        assert count_lines("") == 0

    def test_single_line_no_newline(self):
        assert count_lines("abc") == 1

    def test_single_line_with_newline(self):
        assert count_lines("abc\n") == 1

    def test_two_lines_no_trailing_newline(self):
        assert count_lines("abc\ndef") == 2

    def test_two_lines_with_trailing_newline(self):
        assert count_lines("abc\ndef\n") == 2

    def test_only_newline(self):
        assert count_lines("\n") == 1

    def test_blank_lines(self):
        # 三行（中间空行）
        assert count_lines("a\n\nb\n") == 3


# ============================================================
# slice_lines_by_offset_limit
# ============================================================

class TestSliceLinesByOffsetLimit:
    def _content(self, n: int) -> str:
        """生成 n 行测试文本，每行 'line_NNN'。"""
        return "".join(f"line_{i}\n" for i in range(1, n + 1))

    def test_empty_content(self):
        body, shown, trunc, more = slice_lines_by_offset_limit("", 1, None, 1000)
        assert body == ""
        assert shown is None
        assert trunc == "none"
        assert more is False

    def test_full_read_no_limit_under_cap(self):
        content = self._content(5)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=10000
        )
        assert body == content
        assert shown == (1, 5)
        assert trunc == "none"
        assert more is False

    def test_offset_only(self):
        content = self._content(10)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=3, limit=None, char_cap=10000
        )
        # 从第 3 行到末尾
        assert body.startswith("line_3\n")
        assert body.endswith("line_10\n")
        assert shown == (3, 10)
        assert trunc == "none"
        assert more is False

    def test_limit_binding(self):
        content = self._content(10)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=3, char_cap=10000
        )
        assert body == "line_1\nline_2\nline_3\n"
        assert shown == (1, 3)
        assert trunc == "line_limit"
        assert more is True

    def test_offset_plus_limit(self):
        content = self._content(10)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=5, limit=2, char_cap=10000
        )
        assert body == "line_5\nline_6\n"
        assert shown == (5, 6)
        assert trunc == "line_limit"
        assert more is True

    def test_limit_reaches_end_no_more(self):
        content = self._content(5)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=5, char_cap=10000
        )
        assert shown == (1, 5)
        assert trunc == "none"
        assert more is False

    def test_limit_exceeds_available(self):
        content = self._content(3)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=10, char_cap=10000
        )
        # 只能读到 3 行
        assert shown == (1, 3)
        assert trunc == "none"
        assert more is False

    def test_offset_zero_clamps_to_one(self):
        content = self._content(3)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=0, limit=None, char_cap=10000
        )
        assert shown == (1, 3)

    def test_offset_negative_clamps_to_one(self):
        content = self._content(3)
        body, shown, _, _ = slice_lines_by_offset_limit(
            content, offset=-5, limit=None, char_cap=10000
        )
        assert shown == (1, 3)

    def test_offset_past_eof(self):
        content = self._content(3)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=10, limit=None, char_cap=10000
        )
        assert body == ""
        assert shown is None
        assert trunc == "none"
        assert more is False

    def test_limit_zero_with_more(self):
        """limit=0 合法 → 空切片，但 has_more=True 当后面有内容"""
        content = self._content(3)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=0, char_cap=10000
        )
        assert body == ""
        assert shown is None
        assert trunc == "none"
        assert more is True

    def test_limit_zero_at_eof(self):
        """limit=0 且 offset 已到末尾 → has_more=False"""
        content = self._content(3)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=4, limit=0, char_cap=10000
        )
        assert body == ""
        assert more is False

    def test_char_cap_binding(self):
        """char_cap 比 limit 更先到 → truncated_by=char_limit"""
        content = self._content(100)  # 每行 ~8 字符
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=100, char_cap=20
        )
        # 每行 'line_N\n' = 7-9 字符；20 字符大概能塞 2-3 行
        assert trunc == "char_limit"
        assert more is True
        assert len(body) <= 20

    def test_char_cap_binding_no_limit(self):
        """无 limit 时 char_cap 单独触发"""
        content = self._content(50)
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=30
        )
        assert trunc == "char_limit"
        assert more is True

    def test_single_line_exceeds_cap_hard_truncate(self):
        """单行超 cap → 硬截断到 cap + 末尾标记，后续行仍能 has_more=true。"""
        long_line = "x" * 500
        content = long_line + "\nshort\n"
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=50
        )
        # body = 截断头 + 标记
        assert body.startswith("x" * 50)
        assert "line truncated at 50 chars" in body
        assert "original 501" in body  # 含 \n 的原始长度
        assert "remainder not retrievable" in body
        assert shown == (1, 1)
        assert trunc == "char_limit"
        assert more is True  # 第 2 行还在

    def test_only_single_oversized_line(self):
        """只有一条超大行 → 截断 + 标记 + has_more=false（行级判断）。"""
        content = "x" * 500  # 单行无尾换行
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=10
        )
        assert body.startswith("x" * 10)
        assert "line truncated at 10 chars" in body
        assert "original 500" in body
        assert shown == (1, 1)
        assert trunc == "char_limit"
        assert more is False  # 没有下一行可读

    def test_truncation_marker_does_not_leak_artifact_chars(self):
        """marker 是合成文本，不能让 body 长度暴涨太多——标记本身 ~80 chars。"""
        content = "x" * 1000
        body, _, _, _ = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=100
        )
        # head 100 + marker ~80 = ~180 总长（远小于原始 1000）
        assert len(body) < 200
        assert len(body) > 100  # 至少包含 head + 部分 marker

    def test_no_trailing_newline(self):
        """末尾无换行的内容也能正常处理。"""
        content = "line_1\nline_2\nline_3"  # 末尾无 \n
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=10000
        )
        assert body == content
        assert shown == (1, 3)
        assert more is False
