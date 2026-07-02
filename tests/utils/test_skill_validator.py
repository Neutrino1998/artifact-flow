"""utils.skill_validator 单测:E-1 硬门槛 —— 每条 rule 一正一反 + slug 派生 + 上限边界。

zip 全部内存构造(zipfile.ZipFile(BytesIO, "w"));上限规则经 monkeypatch config 常量
压小值测边界(== 上限过、上限+1 拒)。
"""

import io
import zipfile

import pytest

from config import config
from utils.skill_validator import (
    Finding,
    derive_import_slug,
    slugify_name,
    validate_skill_zip,
    validate_slug,
)

GOOD_MD = """---
name: demo-skill
description: A demo skill.
---

# Demo

Use [the helper](scripts/run.py) to do things.
"""


def build_zip(entries: dict) -> bytes:
    """{member_name: str|bytes} → zip 字节。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def rules_of(result, severity=None):
    return {
        f.rule for f in result.findings if severity is None or f.severity == severity
    }


def assert_clean(result):
    assert result.ok, f"unexpected errors: {[f for f in result.errors]}"


# ---------------------------------------------------------------- 结构 / zip 层


def test_good_zip_passes():
    blob = build_zip({"demo-skill/SKILL.md": GOOD_MD, "demo-skill/scripts/run.py": "print(1)"})
    result = validate_skill_zip(blob, where="t.zip")
    assert_clean(result)
    assert result.parsed is not None
    assert result.parsed.prefix == "demo-skill"
    assert result.parsed.frontmatter["name"] == "demo-skill"
    assert result.parsed.body.startswith("# Demo")


def test_bare_root_layout_passes():
    blob = build_zip({"SKILL.md": GOOD_MD, "scripts/run.py": "print(1)"})
    result = validate_skill_zip(blob, where="t.zip")
    assert_clean(result)
    assert result.parsed.prefix == ""


def test_invalid_zip():
    result = validate_skill_zip(b"not a zip at all", where="t.zip")
    assert rules_of(result, "error") == {"zip.invalid"}
    assert result.parsed is None


def test_skill_md_count_zero_and_multiple():
    r0 = validate_skill_zip(build_zip({"a.txt": "x"}), where="t.zip")
    assert "zip.skill_md_count" in rules_of(r0, "error")
    assert r0.parsed is None
    r2 = validate_skill_zip(
        build_zip({"a/SKILL.md": GOOD_MD, "b/SKILL.md": GOOD_MD}), where="t.zip"
    )
    assert "zip.skill_md_count" in rules_of(r2, "error")


def test_stray_files_outside_prefix():
    blob = build_zip({
        "demo-skill/SKILL.md": GOOD_MD,
        "demo-skill/scripts/run.py": "print(1)",
        "loose.txt": "stray",
    })
    result = validate_skill_zip(blob, where="t.zip")
    assert "zip.stray_files" in rules_of(result, "error")


def test_path_traversal_dotdot_and_absolute():
    for bad in ("../evil.txt", "/abs.txt", "a/../../up.txt"):
        blob = build_zip({"SKILL.md": GOOD_MD, bad: "x"})
        result = validate_skill_zip(blob, where="t.zip")
        assert "zip.path_traversal" in rules_of(result, "error"), bad


def test_symlink_entry_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", GOOD_MD)
        zi = zipfile.ZipInfo("link")
        zi.external_attr = 0o120777 << 16  # S_IFLNK
        zf.writestr(zi, "target")
    result = validate_skill_zip(buf.getvalue(), where="t.zip")
    assert "zip.path_traversal" in rules_of(result, "error")


def test_member_count_cap(monkeypatch):
    monkeypatch.setattr(config, "SKILL_ZIP_MAX_MEMBERS", 3)
    at_cap = build_zip({"SKILL.md": GOOD_MD, "a": "1", "b": "2"})
    assert "zip.too_many_members" not in rules_of(validate_skill_zip(at_cap, where="t"))
    over = build_zip({"SKILL.md": GOOD_MD, "a": "1", "b": "2", "c": "3"})
    assert "zip.too_many_members" in rules_of(validate_skill_zip(over, where="t"), "error")


def test_uncompressed_total_cap(monkeypatch):
    monkeypatch.setattr(config, "SKILL_ZIP_MAX_UNCOMPRESSED_BYTES", 4096)
    over = build_zip({"SKILL.md": GOOD_MD, "big.bin": b"x" * 8192})
    assert "zip.uncompressed_too_large" in rules_of(validate_skill_zip(over, where="t"), "error")


def test_skill_md_member_cap(monkeypatch):
    monkeypatch.setattr(config, "SKILL_MD_MAX_BYTES", 256)
    over = build_zip({"SKILL.md": GOOD_MD + "x" * 512})
    result = validate_skill_zip(over, where="t")
    assert "md.member_too_large" in rules_of(result, "error")
    assert result.parsed is None


def test_skill_md_not_utf8():
    result = validate_skill_zip(build_zip({"SKILL.md": b"\xff\xfe---"}), where="t")
    assert "md.not_utf8" in rules_of(result, "error")
    assert result.parsed is None


# ---------------------------------------------------------------- 正文层


def test_frontmatter_invalid():
    result = validate_skill_zip(build_zip({"SKILL.md": "no frontmatter here"}), where="t")
    assert "md.frontmatter_invalid" in rules_of(result, "error")
    assert result.parsed is None


def test_body_empty_rejected():
    md = "---\nname: x\ndescription: y\n---\n\n   \n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "md.body_empty" in rules_of(result, "error")
    assert result.parsed is not None  # 结构可读,只是正文空


def test_unclosed_fence():
    md = "---\nname: x\n---\n\nbody\n```python\nprint(1)\n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "md.unclosed_fence" in rules_of(result, "error")


def test_closed_fences_and_mixed_markers_pass():
    md = "---\nname: x\n---\n\n```python\n~~~ inside is content\nprint(1)\n```\n\n~~~\ntext\n~~~\n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "md.unclosed_fence" not in rules_of(result)


def test_link_unresolved_and_resolved():
    md = "---\nname: x\n---\n\nSee [a](refs/a.md) and [b](refs/missing.md).\n"
    blob = build_zip({"pkg/SKILL.md": md, "pkg/refs/a.md": "hi"})
    result = validate_skill_zip(blob, where="t")
    errs = [f for f in result.errors if f.rule == "md.link_unresolved"]
    assert len(errs) == 1 and "missing.md" in errs[0].message and "a.md" not in errs[0].message


def test_links_inside_fences_ignored():
    md = "---\nname: x\n---\n\nprose\n```\n[example](not/in/zip.md)\n```\n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "md.link_unresolved" not in rules_of(result)


def test_external_links_skipped():
    md = (
        "---\nname: x\n---\n\n[w](https://x.y) [m](mailto:a@b) [anchor](#sec) "
        "[d](data:text/plain,hi)\n"
    )
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "md.link_unresolved" not in rules_of(result)


def test_orphan_files_warning_and_wheels_exempt():
    md = "---\nname: x\n---\n\nUses [run](scripts/run.py).\n"
    blob = build_zip({
        "SKILL.md": md,
        "scripts/run.py": "print(1)",
        "assets/data.xsd": "<x/>",             # 孤儿 → warning
        "wheels/pkg-1.0-py3-none-any.whl": "b",  # wheels/ 豁免
    })
    result = validate_skill_zip(blob, where="t")
    warns = [f for f in result.warnings if f.rule == "zip.orphan_files"]
    assert len(warns) == 1
    assert "assets/data.xsd" in warns[0].message
    assert "wheels" not in warns[0].message
    assert result.ok  # warning 不挡


def test_body_too_long_warning(monkeypatch):
    monkeypatch.setattr(config, "SKILL_MD_LEGIBILITY_WARN_CHARS", 10)
    blob = build_zip({"SKILL.md": GOOD_MD, "scripts/run.py": "print(1)"})
    result = validate_skill_zip(blob, where="t")
    assert "md.too_long" in rules_of(result, "warning")
    assert result.ok


# ---------------------------------------------------------------- frontmatter 层


@pytest.mark.parametrize(
    "fm,rule",
    [
        ("name: [not, a, string]\ndescription: d", "fm.name_invalid"),
        ("name: ''\ndescription: d", "fm.name_invalid"),
        ("name: x\ndescription: {a: b}", "fm.description_invalid"),
        ("name: x\nallowed-tools: {bad: shape}", "fm.allowed_tools_invalid"),
    ],
)
def test_frontmatter_field_errors(fm, rule):
    md = f"---\n{fm}\n---\n\nbody\n"
    # scripts/run.py 无关;单文件即可
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert rule in rules_of(result, "error")


@pytest.mark.parametrize(
    "fm,rule",
    [
        ("name: x\ncompatibility: 3", "fm.compatibility_invalid"),
        ("name: x\nlicense: [a]", "fm.license_invalid"),
        ("name: x\nmetadata: not-a-dict", "fm.metadata_invalid"),
        ("name: x\nmodel: opus", "fm.cc_extension"),
        ("name: x\nsomething_else: 1", "fm.unknown_keys"),
    ],
)
def test_frontmatter_field_warnings(fm, rule):
    md = f"---\n{fm}\n---\n\nbody\n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert rule in rules_of(result, "warning")
    assert result.ok


def test_name_dir_mismatch_warning():
    md = "---\nname: totally-different\ndescription: d\n---\n\nbody\n"
    blob = build_zip({"wrapper/SKILL.md": md, "wrapper/a.txt": "x"})
    result = validate_skill_zip(blob, where="t")
    assert "fm.name_dir_mismatch" in rules_of(result, "warning")


def test_known_extension_keys_not_flagged():
    md = "---\nname: x\nvisibility: public\ndefault_enabled: false\n---\n\nbody\n"
    result = validate_skill_zip(build_zip({"SKILL.md": md}), where="t")
    assert "fm.unknown_keys" not in rules_of(result)


# ---------------------------------------------------------------- slug


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My Cool Skill", "my-cool-skill"),
        ("docx", "docx"),
        ("  Weird__Name!! ", "weird__name"),
        ("数据处理", ""),  # 全非 ASCII → 空(validate_slug 会拒)
    ],
)
def test_slugify_name(raw, expected):
    assert slugify_name(raw) == expected


def test_derive_import_slug_precedence():
    fm = {"name": "From Name"}
    assert derive_import_slug(fm, "wrapper-dir", "upload.zip") == "from-name"
    assert derive_import_slug({}, "wrapper-dir", "upload.zip") == "wrapper-dir"
    assert derive_import_slug({}, "", "My Upload.zip") == "my-upload"


@pytest.mark.parametrize("slug", ["docx", "a", "x-1_2", "a" * 64])
def test_validate_slug_ok(slug):
    assert validate_slug(slug) is None


@pytest.mark.parametrize("slug", ["", "-lead", "_lead", "UPPER", "has space", "a" * 65, ".skills"])
def test_validate_slug_rejects(slug):
    f = validate_slug(slug)
    assert isinstance(f, Finding) and f.rule == "slug.invalid"
