import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from utils.text_match import find_unique_in_segments


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def text_edit():
    path = ROOT / "sandbox" / "text_edit.py"
    spec = importlib.util.spec_from_file_location("sandbox_text_edit", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_multiline_replace_requires_expected_count(text_edit):
    content = "before\nalpha\nbeta\nafter\n"
    updated, details = text_edit.replace_text(
        content,
        "alpha\nbeta",
        "new\ntext",
        mode="exact",
        expect=1,
    )
    assert updated == "before\nnew\ntext\nafter\n"
    assert details["matches"] == 1

    with pytest.raises(text_edit.TextEditError, match="expected 1 exact match"):
        text_edit.replace_text("x x", "x", "y", mode="exact", expect=1)


def test_operand_reader_strips_one_heredoc_newline(text_edit, tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    blank = tmp_path / "blank.txt"
    lf.write_bytes(b"value\n")
    crlf.write_bytes(b"value\r\n")
    blank.write_bytes(b"value\n\n")
    assert text_edit._read_utf8(lf, strip_final_newline=True) == "value"
    assert text_edit._read_utf8(crlf, strip_final_newline=True) == "value"
    assert text_edit._read_utf8(blank, strip_final_newline=True) == "value\n"


def test_atomic_write_preserves_mode_and_rejects_symlink(text_edit, tmp_path):
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o750)
    text_edit._atomic_write(target, "new", mode=0o750)
    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o777 == 0o750

    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(text_edit.TextEditError, match="symbolic-link output"):
        text_edit._atomic_write(link, "bad", mode=0o644)


def test_cli_failure_leaves_input_untouched(tmp_path):
    target = tmp_path / "source.py"
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    target.write_text("name = 1\nname = 1\n", encoding="utf-8")
    old.write_text("name = 1\n", encoding="utf-8")
    new.write_text("name = 2\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "sandbox" / "text_edit.py"),
            "replace",
            str(target),
            "--old-file",
            str(old),
            "--new-file",
            str(new),
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stderr)
    assert payload["ok"] is False
    assert "found 2" in payload["error"]
    assert target.read_text(encoding="utf-8") == "name = 1\nname = 1\n"


def test_segment_match_is_unique_across_containers():
    normalized = find_unique_in_segments(
        ["前文", "章节Ⅳ结束"], "章节IV结束", mode="normalized"
    )
    assert normalized.success
    assert normalized.segment_index == 1
    assert normalized.match_type == "normalized"

    ambiguous = find_unique_in_segments(
        ["same target", "same target"], "target", mode="auto"
    )
    assert not ambiguous.success
    assert "multiple" in ambiguous.message


def test_segment_fuzzy_match_reports_actual_span():
    result = find_unique_in_segments(
        ["无关段落", "关于人工智能技术的详细介绍。"],
        "关于人工智能枝术的详细介绍",
        mode="auto",
    )
    assert result.success
    assert result.segment_index == 1
    assert result.match_type == "fuzzy"
    assert result.matched_text == "关于人工智能技术的详细介绍"
