#!/usr/bin/env python3
"""Checked, file-based text matching and replacement for sandbox workflows."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

try:
    from text_match import MatchInfo, compute_update
except ModuleNotFoundError as exc:
    if exc.name != "text_match":
        raise
    from utils.text_match import MatchInfo, compute_update


CLI_VERSION = "1.0"


class TextEditError(Exception):
    pass


def _preview(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    half = (limit - 5) // 2
    return text[:half] + "\n...\n" + text[-half:]


def _read_utf8(path: Path, *, strip_final_newline: bool = False) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise TextEditError(f"file not found: {path}") from exc
    except IsADirectoryError as exc:
        raise TextEditError(f"expected a file, got directory: {path}") from exc
    except UnicodeDecodeError as exc:
        raise TextEditError(f"file is not valid UTF-8: {path}") from exc
    if strip_final_newline:
        if text.endswith("\r\n"):
            return text[:-2]
        if text.endswith("\n"):
            return text[:-1]
    return text


def _validate_input(path: Path) -> None:
    if path.is_symlink():
        raise TextEditError(f"symbolic-link input is not supported: {path}")
    if not path.is_file():
        raise TextEditError(f"not a regular file: {path}")


def _atomic_write(path: Path, text: str, *, mode: int) -> None:
    if path.exists() and path.is_symlink():
        raise TextEditError(f"symbolic-link output is not supported: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def locate_text(content: str, needle: str, *, mode: str) -> MatchInfo:
    return compute_update(content, needle, needle, mode=mode)


def replace_text(
    content: str,
    old_text: str,
    new_text: str,
    *,
    mode: str,
    expect: int,
) -> tuple[str, dict]:
    if not old_text:
        raise TextEditError("old text must not be empty")
    if expect < 1:
        raise TextEditError("--expect must be a positive integer")

    if mode == "exact":
        found = content.count(old_text)
        if found != expect:
            raise TextEditError(
                f"expected {expect} exact match(es), found {found}"
            )
        first_offset = content.index(old_text)
        return content.replace(old_text, new_text), {
            "match_type": "exact",
            "matches": found,
            "first_offset": first_offset,
            "similarity": 1.0,
        }

    if expect != 1:
        raise TextEditError("normalized/auto matching requires --expect 1")
    info = compute_update(content, old_text, new_text, mode=mode)
    if not info.success or info.new_content is None:
        raise TextEditError(info.message)
    return info.new_content, {
        "match_type": info.match_type,
        "matches": 1,
        "first_offset": info.offset,
        "similarity": info.similarity,
        "matched_text": _preview(info.matched_text or old_text),
        "fuzzy_stats": info.fuzzy_stats,
    }


def _load_operand(path: str, *, keep_final_newline: bool) -> str:
    return _read_utf8(
        Path(path), strip_final_newline=not keep_final_newline
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text-edit",
        description=(
            "Locate or replace checked UTF-8 text using file-based operands. "
            "Exact matching is the safe default for source and configuration files."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"text-edit {CLI_VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    locate = subparsers.add_parser("locate", help="locate one unique text span")
    locate.add_argument("file")
    locate.add_argument("--needle-file", required=True)
    locate.add_argument(
        "--match", choices=("exact", "normalized", "auto"), default="exact"
    )
    locate.add_argument(
        "--keep-final-newline",
        action="store_true",
        help="treat the operand file's final newline as content",
    )

    replace = subparsers.add_parser("replace", help="replace checked text atomically")
    replace.add_argument("file")
    replace.add_argument("--old-file", required=True)
    replace.add_argument("--new-file", required=True)
    replace.add_argument("--output", help="write another file instead of updating in place")
    replace.add_argument(
        "--match", choices=("exact", "normalized", "auto"), default="exact"
    )
    replace.add_argument("--expect", type=int, default=1)
    replace.add_argument(
        "--keep-final-newline",
        action="store_true",
        help="treat operand files' final newlines as content",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        source = Path(args.file)
        _validate_input(source)
        content = _read_utf8(source)

        if args.command == "locate":
            needle = _load_operand(
                args.needle_file,
                keep_final_newline=args.keep_final_newline,
            )
            info = locate_text(content, needle, mode=args.match)
            if not info.success:
                raise TextEditError(info.message)
            result = {
                "ok": True,
                "operation": "locate",
                "file": str(source),
                "match_type": info.match_type,
                "offset": info.offset,
                "length": info.deleted_len,
                "similarity": info.similarity,
                "matched_text": _preview(info.matched_text or needle),
                "fuzzy_stats": info.fuzzy_stats,
            }
        else:
            old_text = _load_operand(
                args.old_file,
                keep_final_newline=args.keep_final_newline,
            )
            new_text = _load_operand(
                args.new_file,
                keep_final_newline=args.keep_final_newline,
            )
            updated, details = replace_text(
                content,
                old_text,
                new_text,
                mode=args.match,
                expect=args.expect,
            )
            output = Path(args.output) if args.output else source
            source_mode = stat.S_IMODE(source.stat().st_mode)
            _atomic_write(output, updated, mode=source_mode)
            result = {
                "ok": True,
                "operation": "replace",
                "file": str(source),
                "output": str(output),
                "old_chars": len(old_text),
                "new_chars": len(new_text),
                **details,
            }
    except (OSError, ValueError, TextEditError) as exc:
        print(json.dumps({
            "ok": False,
            "operation": args.command,
            "error": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
