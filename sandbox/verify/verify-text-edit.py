#!/usr/bin/env python3
"""In-container smoke test for text-edit and its RapidFuzz path."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


def run(*args: str, expect: int = 0):
    result = subprocess.run(
        ["text-edit", *args], text=True, capture_output=True, check=False
    )
    if result.returncode != expect:
        raise RuntimeError(
            f"text-edit returned {result.returncode}, expected {expect}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    stream = result.stdout if expect == 0 else result.stderr
    return json.loads(stream)


with tempfile.TemporaryDirectory(prefix="text-edit-verify-") as temp_dir:
    root = Path(temp_dir)
    target = root / "source.txt"
    old = root / "old.txt"
    new = root / "new.txt"

    target.write_text("before\nold block\nafter\n", encoding="utf-8")
    old.write_text("old block\n", encoding="utf-8")
    new.write_text("new block\n", encoding="utf-8")
    payload = run(
        "replace", str(target), "--old-file", str(old), "--new-file", str(new)
    )
    assert payload["match_type"] == "exact"
    assert target.read_text(encoding="utf-8") == "before\nnew block\nafter\n"

    target.write_text("same same", encoding="utf-8")
    old.write_text("same\n", encoding="utf-8")
    before = target.read_bytes()
    failed = run(
        "replace",
        str(target),
        "--old-file",
        str(old),
        "--new-file",
        str(new),
        expect=1,
    )
    assert failed["ok"] is False
    assert target.read_bytes() == before

    target.write_text("关于人工智能技术的详细介绍。", encoding="utf-8")
    old.write_text("关于人工智能枝术的详细介绍\n", encoding="utf-8")
    new.write_text("已更新\n", encoding="utf-8")
    payload = run(
        "replace",
        str(target),
        "--old-file",
        str(old),
        "--new-file",
        str(new),
        "--match",
        "auto",
    )
    assert payload["match_type"] == "fuzzy"
    assert target.read_text(encoding="utf-8") == "已更新。"

print("text-edit: exact/ambiguous/fuzzy probes passed")
