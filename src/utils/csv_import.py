"""
CSV 批量导入用户的解析工具。

职责：
- 字节流 → 文本（charset-normalizer 识别 UTF-8 / UTF-8 BOM / GBK 等）
- DictReader 解析 + header 列名标准化（lower + strip）
- 行数上限检查
- 文件内 username 重复检测（preflight 整体 reject 用）
- 已知 / 未知列分流（未知列收集到 warnings，不阻断）

不做：
- username 格式 / department gap / DB 查重 — 这些是业务校验，留给路由层
- 任何 DB 访问 — 纯无副作用工具

设计理由：把"解析失败 vs 业务失败"分开 —— 解析阶段失败 = 整体 400（CSV
本身坏），业务阶段失败 = 单行 failed/skipped（best-effort）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Optional


KNOWN_COLUMNS = {
    "username",
    "password",
    "display_name",
    "dept_l1",
    "dept_l2",
    "dept_l3",
}

REQUIRED_COLUMNS = {"username"}

# 字段长度上限 — 与 DB 列宽 / Pydantic schema max_length 对齐。
# Source of truth: User.display_name = String(128), Department.name = String(128),
# CreateUserRequest.password = max_length=128.
# 普通 API 走 Pydantic schema 自动拦下；CSV 路径绕过 schema，必须在 importer
# 里复刻这层校验，否则 PG/MySQL 在 INSERT 时会炸 500（SQLite VARCHAR 不强制）。
DISPLAY_NAME_MAX = 128
DEPT_NAME_MAX = 128
PASSWORD_MAX = 128


class CsvParseError(ValueError):
    """CSV 解析期错误（路由层应转 400）。"""


@dataclass
class ParsedRow:
    """解析出的单行 — 列名已标准化，值已 strip。空字符串保留为 ''。"""

    row_number: int  # 1-based 数据行号（不含 header）
    username: str
    password: str
    display_name: str
    dept_l1: str
    dept_l2: str
    dept_l3: str


@dataclass
class ParsedCsv:
    rows: list[ParsedRow]
    detected_encoding: Optional[str]
    unknown_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicate_rows: list[tuple[int, str]] = field(default_factory=list)
    """文件内 username 重复 → list of (row_number, username)。非空时路由层应整体 reject。"""


def _decode(file_bytes: bytes) -> tuple[str, Optional[str]]:
    """charset-normalizer 解码 → (text, encoding_name)。失败抛 CsvParseError。"""
    if not file_bytes:
        raise CsvParseError("Empty file")

    from charset_normalizer import from_bytes

    result = from_bytes(file_bytes)
    best = result.best()
    if best is None:
        raise CsvParseError("Cannot decode file: not a valid text file")
    return str(best), best.encoding


def _normalize_header(raw: list[str]) -> list[str]:
    """header 列名 lower + strip + 去 BOM 残留（UTF-8 BOM 已被 charset-normalizer
    解码，但 csv.DictReader 对 \\ufeff 不处理首列名前缀）。"""
    out: list[str] = []
    for col in raw:
        if col is None:
            out.append("")
            continue
        s = col.strip().lstrip("﻿").lower()
        out.append(s)
    return out


def parse_user_csv(file_bytes: bytes, max_rows: int) -> ParsedCsv:
    """
    解析批量导入 CSV。

    Args:
        file_bytes: 上传文件原始字节
        max_rows: 数据行上限（不含 header）；超过抛 CsvParseError

    Returns:
        ParsedCsv with rows / unknown_columns / warnings / duplicate_rows

    Raises:
        CsvParseError: 解码失败 / header 缺 username / 行数超限
            （duplicate_rows 非空不抛，留给路由层决定如何 reject）
    """
    text, encoding = _decode(file_bytes)

    reader = csv.reader(io.StringIO(text))
    try:
        raw_header = next(reader)
    except StopIteration:
        raise CsvParseError("CSV has no header row")

    header = _normalize_header(raw_header)
    header_set = set(header)

    missing = REQUIRED_COLUMNS - header_set
    if missing:
        raise CsvParseError(
            f"CSV missing required column(s): {sorted(missing)}"
        )

    unknown_cols = sorted(header_set - KNOWN_COLUMNS - {""})
    warnings: list[str] = []
    if unknown_cols:
        warnings.append(f"Ignored unknown columns: {unknown_cols}")

    # 建 col-name → index 映射，缺失列查找时返回 ''
    col_idx: dict[str, int] = {}
    for i, name in enumerate(header):
        if name in KNOWN_COLUMNS and name not in col_idx:
            col_idx[name] = i

    def _cell(row: list[str], col: str) -> str:
        idx = col_idx.get(col)
        if idx is None or idx >= len(row):
            return ""
        v = row[idx]
        return v.strip() if v is not None else ""

    rows: list[ParsedRow] = []
    seen_usernames: dict[str, int] = {}  # username → first row_number
    duplicate_rows: list[tuple[int, str]] = []
    data_row_number = 0

    for raw_row in reader:
        # 全空行（含全空格）跳过 — Excel 导出常带尾随空行
        if not any((cell or "").strip() for cell in raw_row):
            continue

        data_row_number += 1
        if data_row_number > max_rows:
            raise CsvParseError(
                f"CSV exceeds row limit: {max_rows} rows (excluding header)"
            )

        username = _cell(raw_row, "username")
        parsed = ParsedRow(
            row_number=data_row_number,
            username=username,
            password=_cell(raw_row, "password"),
            display_name=_cell(raw_row, "display_name"),
            dept_l1=_cell(raw_row, "dept_l1"),
            dept_l2=_cell(raw_row, "dept_l2"),
            dept_l3=_cell(raw_row, "dept_l3"),
        )
        rows.append(parsed)

        # 文件内重复检测（仅对非空 username 生效）
        if username:
            if username in seen_usernames:
                duplicate_rows.append((data_row_number, username))
            else:
                seen_usernames[username] = data_row_number

    return ParsedCsv(
        rows=rows,
        detected_encoding=encoding,
        unknown_columns=unknown_cols,
        warnings=warnings,
        duplicate_rows=duplicate_rows,
    )
