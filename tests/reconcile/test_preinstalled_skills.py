"""预装 skill 集(E-4)回归 —— 三道闸:

1. **防漂移**:config/skills/<slug>.zip 必须与从 config/skills-src/<slug>/
   重建的结果**内容等价**(manifest:成员路径/时间戳/属性/解压字节)。改了
   skills-src 忘跑 `python scripts/build_skill_zips.py` 时在这里响。
   不比压缩字节 —— DEFLATE 输出依赖 zlib 实现(zlib-ng 机器会假失败)。
2. **过自家硬门**:每个预装 zip 过 E-1 validator 必须零 error 零 warning
   (预装集是"真语料验证",warning 也不许 —— 用户导入才允许 warning 放行)。
3. **seed 解析干净**:parse_skill_seeds 吃下 config/skills/ 全量不抛 SeedError,
   且预装 slug 都在、public + default_enabled(用户开箱即在 L1),单文件 skill 也有
   可下载 bundle,但 has_extra_files=False。
"""

import importlib.util
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from reconcile.seeds import parse_skill_seeds
from utils.skill_validator import validate_skill_zip

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "config" / "skills-src"
ZIP_DIR = ROOT / "config" / "skills"
PREINSTALLED = ["dataviz", "docx", "pdf", "pptx", "skill-creator", "xlsx"]
# 纯散文预装(SKILL.md-only 目录源码;入库时也会生成单文件 zip bundle)
PREINSTALLED_PROSE = ["html-artifact-design"]


def _build_zip(slug: str) -> bytes:
    spec = importlib.util.spec_from_file_location(
        "build_skill_zips", ROOT / "scripts" / "build_skill_zips.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_zip(slug)


def _load_skill_script(slug: str, rel: str):
    path = SRC_DIR / slug / rel
    spec = importlib.util.spec_from_file_location(f"{slug}_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_src_dirs_and_zips_in_sync_roster():
    src = sorted(p.name for p in SRC_DIR.iterdir() if p.is_dir())
    assert src == PREINSTALLED, "skills-src 目录清单变了 —— 同步更新本测试与预装集"
    for slug in PREINSTALLED:
        assert (ZIP_DIR / f"{slug}.zip").is_file(), f"{slug}.zip 未构建"


def _zip_manifest(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return [
            (i.filename, i.date_time, i.external_attr, i.create_system,
             zf.read(i.filename))
            for i in sorted(zf.infolist(), key=lambda i: i.filename)
        ]


@pytest.mark.parametrize("slug", PREINSTALLED)
def test_committed_zip_matches_rebuild(slug):
    committed = (ZIP_DIR / f"{slug}.zip").read_bytes()
    assert _zip_manifest(committed) == _zip_manifest(_build_zip(slug)), (
        f"{slug}.zip 与 skills-src 重建结果内容不一致 —— "
        "改了源没跑 scripts/build_skill_zips.py"
    )


@pytest.mark.parametrize("slug", PREINSTALLED)
def test_zip_passes_validator_clean(slug):
    result = validate_skill_zip(
        (ZIP_DIR / f"{slug}.zip").read_bytes(), where=f"{slug}.zip")
    assert result.ok, [f"{f.rule}: {f.message}" for f in result.errors]
    assert not result.warnings, [
        f"{f.rule}: {f.message}" for f in result.warnings
    ]  # 预装集零 warning:orphan/链接悬空这类在策展期就该清掉


def test_seed_parse_clean_and_defaults():
    seeds = parse_skill_seeds(
        str(ZIP_DIR), known_unit_names=set(), known_full_names={})
    by_slug = {s.slug: s for s in seeds}
    for slug in PREINSTALLED + PREINSTALLED_PROSE:
        assert slug in by_slug, f"seed 解析缺 {slug}"
        seed = by_slug[slug]
        assert seed.visibility == "public"
        assert seed.default_enabled is True
        is_prose = slug in PREINSTALLED_PROSE
        assert seed.bundle is not None
        assert seed.has_extra_files is (not is_prose)
        assert _zip_manifest(seed.bundle)
        assert seed.skill_md.strip()


def test_preinstalled_skill_scripts_are_syntax_valid():
    scripts = sorted(SRC_DIR.glob("*/scripts/**/*.py"))
    assert scripts, "预装 skill 应至少包含脚本,否则本 smoke 失效"
    for path in scripts:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_docx_apply_redline_smoke(tmp_path):
    docx = pytest.importorskip("docx")
    etree = pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/apply_redline.py")

    src = tmp_path / "in.docx"
    out = tmp_path / "out.docx"
    document = docx.Document()
    document.add_paragraph("Hello old world")
    document.save(src)

    summary = mod.apply_redline(
        src, out, needle="old", mode="replace", new_text="new",
        author="Review", all_matches=False,
    )
    assert summary["changes"] == 1
    with zipfile.ZipFile(out) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    assert root.xpath(".//w:del//w:delText[text()='old']", namespaces=ns)
    assert root.xpath(".//w:ins//w:t[text()='new']", namespaces=ns)

    insert_src = tmp_path / "insert-in.docx"
    insert_out = tmp_path / "insert-out.docx"
    document = docx.Document()
    document.add_paragraph("Hello anchor world")
    document.save(insert_src)

    mod.apply_redline(
        insert_src, insert_out, needle="anchor", mode="insert-after",
        new_text=" NEW", author="Review", all_matches=False,
    )
    with zipfile.ZipFile(insert_out) as zf:
        insert_root = etree.fromstring(zf.read("word/document.xml"))
    paragraph = insert_root.xpath(".//w:body/w:p", namespaces=ns)[0]
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    tokens = []
    for child in paragraph:
        if child.tag == w + "r":
            tokens.append((
                "text",
                "".join(t.text or "" for t in child.findall(w + "t")),
            ))
        elif child.tag == w + "ins":
            tokens.append((
                "ins",
                "".join(child.xpath(".//w:t/text()", namespaces=ns)),
            ))
    assert tokens[:3] == [
        ("text", "Hello anchor"),
        ("ins", " NEW"),
        ("text", " world"),
    ]


def _shape_texts(shape_items):
    texts = []
    for shape in shape_items:
        if shape.get("text"):
            texts.append(shape["text"])
        texts.extend(_shape_texts(shape.get("children", [])))
    return texts


def test_pptx_inspect_and_replace_text_smoke(tmp_path):
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    check_mod = _load_skill_script("pptx", "scripts/check_geometry.py")
    inspect_mod = _load_skill_script("pptx", "scripts/inspect_deck.py")
    replace_mod = _load_skill_script("pptx", "scripts/replace_text.py")

    with pytest.raises(SystemExit, match="--find requires --replace"):
        replace_mod._load_replacements(
            SimpleNamespace(map=None, find="Old", replace=None))

    src = tmp_path / "in.pptx"
    out = tmp_path / "out.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    group = slide.shapes.add_group_shape()
    box = group.shapes.add_textbox(
        util.Inches(1), util.Inches(1), util.Inches(6), util.Inches(1))
    box.text_frame.paragraphs[0].text = "Grouped Old title"
    prs.save(src)

    summary = replace_mod.replace_text(
        src, out, [("Old", "New")], slides=None, allow_missing=False)
    assert summary["total_hits"] == 1
    inspected = inspect_mod.inspect(out, max_text=100, include_notes=False)
    top_level_shapes = inspected["slides"][0]["shapes"]
    groups = [shape for shape in top_level_shapes if shape["type"] == "group"]
    assert groups and groups[0]["child_count"] == 1
    assert any("Grouped New title" in text for text in _shape_texts(top_level_shapes))

    geometry = check_mod.check_geometry(str(out))
    assert geometry["issues"] == []
