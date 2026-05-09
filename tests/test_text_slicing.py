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

    def test_single_line_exceeds_cap_force_return(self):
        """边界：第一行就比 cap 还长 → 强制返回该行（避免空 body 卡死）"""
        content = "this_is_a_very_long_single_line_that_exceeds_the_cap\nshort\n"
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=10
        )
        # 必须返回非空 body，否则模型无法分页前进
        assert body != ""
        assert shown == (1, 1)
        assert trunc == "char_limit"
        assert more is True

    def test_no_trailing_newline(self):
        """末尾无换行的内容也能正常处理。"""
        content = "line_1\nline_2\nline_3"  # 末尾无 \n
        body, shown, trunc, more = slice_lines_by_offset_limit(
            content, offset=1, limit=None, char_cap=10000
        )
        assert body == content
        assert shown == (1, 3)
        assert more is False
