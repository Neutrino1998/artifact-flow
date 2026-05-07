"""Tests for utils.csv_import.parse_user_csv (PR3)."""

import pytest

from utils.csv_import import CsvParseError, parse_user_csv


def _csv(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


class TestParseUserCsvBasics:
    def test_empty_file_raises(self):
        with pytest.raises(CsvParseError, match="Empty file"):
            parse_user_csv(b"", max_rows=100)

    def test_header_missing_username_raises(self):
        data = _csv("password,display_name\nfoo,Bar\n")
        with pytest.raises(CsvParseError, match="missing required column"):
            parse_user_csv(data, max_rows=100)

    def test_only_header_returns_empty_rows(self):
        data = _csv("username,password\n")
        parsed = parse_user_csv(data, max_rows=100)
        assert parsed.rows == []
        assert parsed.duplicate_rows == []

    def test_header_case_insensitive_and_strip(self):
        data = _csv("  Username , Password , Display_Name \nalice,secret,Alice\n")
        parsed = parse_user_csv(data, max_rows=100)
        assert len(parsed.rows) == 1
        r = parsed.rows[0]
        assert r.username == "alice"
        assert r.password == "secret"
        assert r.display_name == "Alice"

    def test_optional_columns_subset(self):
        # Only username column present
        data = _csv("username\nalice\nbob\n")
        parsed = parse_user_csv(data, max_rows=100)
        assert [r.username for r in parsed.rows] == ["alice", "bob"]
        assert all(r.password == "" for r in parsed.rows)
        assert all(r.dept_l1 == "" for r in parsed.rows)


class TestParseUserCsvEncoding:
    def test_utf8_with_bom(self):
        # UTF-8 BOM ﻿ at the start of the file (Excel save-as default)
        text = "username,display_name\nalice,小爱\n"
        data = b"\xef\xbb\xbf" + text.encode("utf-8")
        parsed = parse_user_csv(data, max_rows=100)
        assert len(parsed.rows) == 1
        assert parsed.rows[0].username == "alice"
        assert parsed.rows[0].display_name == "小爱"

    def test_gbk_chinese_display_name(self):
        # Realistic-sized Chinese sample — charset-normalizer detection is
        # unstable on very short snippets, so use enough text to look like a
        # real Excel-saved-as-CSV file from a Chinese-locale Windows machine.
        text = (
            "username,display_name,dept_l1,dept_l2\n"
            "alice,张小爱,技术部,后端组\n"
            "bob,王大明,产品部,需求组\n"
            "carol,刘晓红,设计部,视觉组\n"
            "david,陈志强,运营部,内容组\n"
        )
        data = text.encode("gbk")
        parsed = parse_user_csv(data, max_rows=100)
        assert parsed.detected_encoding is not None
        assert len(parsed.rows) == 4
        assert parsed.rows[0].username == "alice"
        assert parsed.rows[0].display_name == "张小爱"
        assert parsed.rows[0].dept_l1 == "技术部"


class TestParseUserCsvLimits:
    def test_row_count_at_limit_ok(self):
        rows = "\n".join(f"user{i:04d}" for i in range(50))
        data = _csv(f"username\n{rows}\n")
        parsed = parse_user_csv(data, max_rows=50)
        assert len(parsed.rows) == 50

    def test_row_count_over_limit_raises(self):
        rows = "\n".join(f"user{i:04d}" for i in range(51))
        data = _csv(f"username\n{rows}\n")
        with pytest.raises(CsvParseError, match="exceeds row limit"):
            parse_user_csv(data, max_rows=50)


class TestParseUserCsvFiltering:
    def test_blank_rows_skipped(self):
        # Mid-file blank line + trailing blank lines (Excel artifact) are dropped
        data = _csv("username,password\nalice,a\n\n  ,  \nbob,b\n\n")
        parsed = parse_user_csv(data, max_rows=100)
        assert [r.username for r in parsed.rows] == ["alice", "bob"]
        # row_number is data-row-only, so alice=1, bob=2 (blanks don't bump it)
        assert parsed.rows[0].row_number == 1
        assert parsed.rows[1].row_number == 2

    def test_duplicate_within_file_collected_not_raised(self):
        data = _csv("username\nalice\nbob\nalice\nalice\n")
        parsed = parse_user_csv(data, max_rows=100)
        # duplicate_rows holds the *second* occurrence and onwards
        assert parsed.duplicate_rows == [(3, "alice"), (4, "alice")]
        # rows still includes all entries — caller decides how to react
        assert len(parsed.rows) == 4

    def test_unknown_columns_warning_not_error(self):
        data = _csv("username,password,notes,extra\nalice,a,hello,x\n")
        parsed = parse_user_csv(data, max_rows=100)
        assert parsed.unknown_columns == ["extra", "notes"]
        assert any("Ignored unknown columns" in w for w in parsed.warnings)
        # Known columns still parsed
        assert parsed.rows[0].username == "alice"
        assert parsed.rows[0].password == "a"

    def test_dept_columns_parsed(self):
        data = _csv(
            "username,dept_l1,dept_l2,dept_l3\n"
            "alice,部门A,子部门A1,小组A1a\n"
            "bob,部门B,,\n"
        )
        parsed = parse_user_csv(data, max_rows=100)
        assert parsed.rows[0].dept_l1 == "部门A"
        assert parsed.rows[0].dept_l2 == "子部门A1"
        assert parsed.rows[0].dept_l3 == "小组A1a"
        assert parsed.rows[1].dept_l1 == "部门B"
        assert parsed.rows[1].dept_l2 == ""
        assert parsed.rows[1].dept_l3 == ""

    def test_value_strip(self):
        data = _csv("username,display_name\n  alice  ,  Alice Cooper  \n")
        parsed = parse_user_csv(data, max_rows=100)
        assert parsed.rows[0].username == "alice"
        assert parsed.rows[0].display_name == "Alice Cooper"
