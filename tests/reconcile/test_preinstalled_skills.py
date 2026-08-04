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

import base64
import importlib.util
import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from reconcile.seeds import parse_skill_seeds
from utils.skill_validator import validate_skill_zip

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "config" / "skills-src"
ZIP_DIR = ROOT / "config" / "skills"
PREINSTALLED = [
    "dataviz", "docx", "mermaid-to-png", "pdf", "pptx", "skill-creator", "xlsx",
]
# skills-src 内的 SKILL.md-only 预装包。仍产 zip 供下载，has_extra_files=False。
PREINSTALLED_SINGLE_FILE = {"mermaid-to-png"}
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


def _word_media_members(docx_path: Path) -> list[str]:
    with zipfile.ZipFile(docx_path) as zf:
        return sorted(
            name for name in zf.namelist() if name.startswith("word/media/")
        )


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
        is_single_file = slug in PREINSTALLED_PROSE or slug in PREINSTALLED_SINGLE_FILE
        assert seed.bundle is not None
        assert seed.has_extra_files is (not is_single_file)
        assert _zip_manifest(seed.bundle)
        assert seed.skill_md.strip()


def test_mermaid_to_png_routing_contract():
    skill_md = (SRC_DIR / "mermaid-to-png" / "SKILL.md").read_text(encoding="utf-8")
    lead_md = (ROOT / "config" / "agents" / "lead_agent.md").read_text(
        encoding="utf-8"
    )

    assert "仅在需要 PNG 文件时激活" in skill_md
    assert "Mermaid 源码和 SVG 下载不激活" in skill_md
    assert "本技能只负责生成 PNG" in skill_md
    assert "不调用 `bash`、`mount` 或 `persist`" in skill_md
    assert "`.mmd` 是 `/workspace` 中的临时渲染输入" in skill_md
    assert "不要 `persist`" in skill_md
    assert "`persist` 源" not in skill_md
    assert "不要在 PNG 标签中使用 emoji" in skill_md
    assert "emoji 同行的中文一起变成方块" in skill_md
    assert "不要把中文整体改成英文" in skill_md
    assert "不要尝试 `apt-get`" in skill_md
    assert "或 `fc-cache`" in skill_md
    assert "```mermaid fenced code block" in lead_md


def test_dataviz_font_contract():
    skill_md = (SRC_DIR / "dataviz" / "SKILL.md").read_text(encoding="utf-8")

    assert "Matplotlib 图表直接支持普通中文" in skill_md
    assert "镜像不提供" in skill_md
    assert "emoji 字体" in skill_md
    assert "不要在标题、轴标签或标注中用 emoji" in skill_md
    assert "`Glyph ... missing from font(s)`" in skill_md
    assert "emoji 缺字不会连带破坏同行中文" in skill_md


def test_pdf_skill_large_document_memory_contract():
    skill_md = (SRC_DIR / "pdf" / "SKILL.md").read_text(encoding="utf-8")

    assert "`pages_text = []`" in skill_md
    assert "`text += ...`" in skill_md
    assert "只限制工具输出" in skill_md
    assert "`page.close()`" in skill_md
    assert "`rg -n -C`" in skill_md


def test_document_skills_use_risk_bounded_visual_verification():
    docx_md = (SRC_DIR / "docx" / "SKILL.md").read_text(encoding="utf-8")
    docx_decompose = (
        SRC_DIR / "docx" / "scripts" / "decompose_docx.py"
    ).read_text(encoding="utf-8")
    pdf_md = (SRC_DIR / "pdf" / "SKILL.md").read_text(encoding="utf-8")
    pptx_md = (SRC_DIR / "pptx" / "SKILL.md").read_text(encoding="utf-8")
    xlsx_md = (SRC_DIR / "xlsx" / "SKILL.md").read_text(encoding="utf-8")
    vision_md = (ROOT / "config" / "agents" / "_vision_agent.md").read_text(
        encoding="utf-8"
    )

    for skill_md in (docx_md, pdf_md, pptx_md, xlsx_md):
        assert "风险驱动的最小范围" in skill_md
        assert "用户已反馈视觉问题" in skill_md
        assert "不宣称已逐页验证" in skill_md

    assert "不因文档含图就渲染全文" in pdf_md
    assert "不要因此渲染全文" in docx_md
    assert "不要把同一文件换 ID 重试" in docx_md
    assert "decompose_docx.py" in docx_md
    assert "`document.md` 是唯一内容来源" in docx_md
    assert "`include_in_current_view: true`" in docx_md
    assert "脚本不会输出未经显示变换的原始媒体" in docx_md
    assert "`source_part` 只是 DOCX 包内定位元数据" in docx_md
    assert "`revision_state`" in docx_md
    assert "脚本不另建表格 JSON" in docx_md
    assert "合成 ID 只在本次" in docx_md
    assert "catalog_tables.lua" in docx_decompose
    assert "document.ast.json" not in docx_decompose
    assert "--pages 5,8" in docx_md
    assert "不要因演示文稿含图片就把所有页面交给视觉能力" in pptx_md
    assert "persist` 为 image artifacts" in pptx_md
    assert "不能读取调用方沙盒中的 `/workspace/...` 路径" in pptx_md
    assert "普通数据读取、公式分析和值修改默认不渲染" in xlsx_md
    assert "source-format skill decides whether to extract" in vision_md
    assert "pass image artifact id(s) only" in vision_md
    assert "Do not try to recover with `bash`, `mount`, `read_skill`" in vision_md
    assert "report the actual page/title cues briefly and stop" in vision_md


def test_preinstalled_skill_scripts_are_syntax_valid():
    scripts = sorted(SRC_DIR.glob("*/scripts/**/*.py"))
    assert scripts, "预装 skill 应至少包含脚本,否则本 smoke 失效"
    for path in scripts:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_docx_decompose_materializes_visible_crop_and_flags_fallback(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/decompose_docx.py")

    image_buffer = io.BytesIO()
    Image.new("RGB", (100, 40), "red").save(image_buffer, format="PNG")
    footnote_image_buffer = io.BytesIO()
    Image.new("RGB", (20, 10), "blue").save(
        footnote_image_buffer, format="PNG"
    )
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{mod.W_NS}" xmlns:r="{mod.R_NS}"
 xmlns:a="{mod.A_NS}" xmlns:pic="{mod.PIC_NS}" xmlns:wp="{mod.WP_NS}"
 xmlns:v="{mod.V_NS}" xmlns:mc="{mod.MC_NS}" xmlns:wps="{mod.WPS_NS}">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Section</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Visible crop</w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:extent cx="100" cy="40"/>
        <wp:docPr id="1" name="Architecture image" descr="Visible architecture"/>
        <a:graphic><a:graphicData><pic:pic>
          <pic:blipFill><a:blip r:embed="rId1"/>
            <a:srcRect l="25000" r="25000"/>
            <a:stretch><a:fillRect/></a:stretch>
          </pic:blipFill>
          <pic:spPr><a:xfrm/><a:prstGeom prst="rect"/></pic:spPr>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing></w:r>
      <w:r><w:footnoteReference w:id="1"/></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Rotated fallback</w:t></w:r>
      <w:r><w:drawing><wp:inline><wp:extent cx="100" cy="40"/>
        <a:graphic><a:graphicData><pic:pic>
          <pic:blipFill><a:blip r:embed="rId1"/>
            <a:stretch><a:fillRect/></a:stretch>
          </pic:blipFill>
          <pic:spPr><a:xfrm rot="5400000"/><a:prstGeom prst="rect"/></pic:spPr>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Alternate image</w:t></w:r>
      <mc:AlternateContent>
        <mc:Choice Requires="wps">
          <w:r><w:t xml:space="preserve"> selected text</w:t></w:r>
          <w:r><w:drawing><wp:inline>
          <wp:extent cx="100" cy="40"/>
          <a:graphic><a:graphicData><pic:pic>
            <pic:blipFill><a:blip r:embed="rId1"/>
              <a:stretch><a:fillRect/></a:stretch>
            </pic:blipFill>
            <pic:spPr><a:xfrm/><a:prstGeom prst="rect"/></pic:spPr>
          </pic:pic></a:graphicData></a:graphic>
        </wp:inline></w:drawing></w:r></mc:Choice>
        <mc:Fallback>
          <w:r><w:t xml:space="preserve"> fallback text</w:t></w:r>
          <w:r><w:pict><v:shape><v:imagedata r:id="rId1"/></v:shape></w:pict>
            <w:object><o:OLEObject
              xmlns:o="urn:schemas-microsoft-com:office:office"/></w:object>
          </w:r>
        </mc:Fallback>
      </mc:AlternateContent>
    </w:p>
    <mc:AlternateContent>
      <mc:Choice Requires="wps"><w:tbl>
        <w:tblPr><w:tblCaption w:val="MCE Choice catalog"/></w:tblPr>
        <w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid>
        <w:tr><w:tc><w:p><w:r><w:t>MCE Choice table</w:t></w:r></w:p>
        </w:tc></w:tr>
      </w:tbl></mc:Choice>
      <mc:Fallback><w:p><w:r><w:t>MCE fallback table</w:t></w:r></w:p>
      </mc:Fallback>
    </mc:AlternateContent>
    <w:p><w:del w:author="Reviewer" w:date="2026-01-01T00:00:00Z">
      <w:r><w:delText>Deleted image</w:delText></w:r>
      <w:r><w:drawing><wp:inline><wp:extent cx="100" cy="40"/>
        <a:graphic><a:graphicData><pic:pic>
          <pic:blipFill><a:blip r:embed="rId1"/>
            <a:stretch><a:fillRect/></a:stretch>
          </pic:blipFill>
          <pic:spPr><a:xfrm/><a:prstGeom prst="rect"/></pic:spPr>
        </pic:pic></a:graphicData></a:graphic>
      </wp:inline></w:drawing></w:r>
    </w:del></w:p>
    <w:tbl><w:tblGrid><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/>
      <w:gridCol w:w="1000"/></w:tblGrid>
      <w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr><w:tc>
      <w:tcPr><w:gridSpan w:val="2"/></w:tcPr>
      <w:p><w:r><w:t>Merged cell</w:t></w:r></w:p>
    </w:tc></w:tr></w:tbl>
    <w:tbl><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc>
      <w:p><w:r><w:t>Outer cell</w:t></w:r></w:p>
      <w:tbl><w:tblGrid><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/>
        </w:tblGrid><w:tr><w:tc>
        <w:p><w:r><w:t>Nested A</w:t></w:r></w:p>
      </w:tc><w:tc>
        <w:p><w:r><w:t>Nested B</w:t></w:r></w:p>
      </w:tc></w:tr></w:tbl>
      <w:p/>
    </w:tc></w:tr></w:tbl>
    <w:tbl><w:tblGrid><w:gridCol w:w="1000"/></w:tblGrid><w:tr><w:tc><w:p>
      <w:del w:author="Reviewer" w:date="2026-01-01T00:00:00Z">
        <w:r><w:delText>old</w:delText></w:r>
      </w:del>
      <w:ins w:author="Reviewer" w:date="2026-01-01T00:00:00Z">
        <w:r><w:t>new</w:t></w:r>
      </w:ins>
    </w:p></w:tc></w:tr></w:tbl>
    <w:sdt><w:sdtContent><w:tbl><w:tblGrid><w:gridCol w:w="1000"/>
      </w:tblGrid><w:tr><w:tc><w:p>
      <w:r><w:t>Content control table</w:t></w:r>
    </w:p></w:tc></w:tr></w:tbl></w:sdtContent></w:sdt>
    <w:sectPr/>
  </w:body>
</w:document>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>
"""
    package_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""
    styles_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{mod.W_NS}">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/>
  </w:style>
</w:styles>
"""
    footnotes_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{mod.W_NS}" xmlns:r="{mod.R_NS}"
 xmlns:a="{mod.A_NS}" xmlns:pic="{mod.PIC_NS}" xmlns:wp="{mod.WP_NS}">
  <w:footnote w:type="separator" w:id="-1">
    <w:p><w:r><w:separator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:type="continuationSeparator" w:id="0">
    <w:p><w:r><w:continuationSeparator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:id="1"><w:p>
    <w:r><w:t>Footnote image</w:t></w:r>
    <w:r><w:drawing><wp:inline><wp:extent cx="20" cy="10"/>
      <a:graphic><a:graphicData><pic:pic>
        <pic:blipFill><a:blip r:embed="rId1"/>
          <a:stretch><a:fillRect/></a:stretch>
        </pic:blipFill>
        <pic:spPr><a:xfrm/><a:prstGeom prst="rect"/></pic:spPr>
      </pic:pic></a:graphicData></a:graphic>
    </wp:inline></w:drawing></w:r>
  </w:p></w:footnote>
</w:footnotes>
"""
    footnotes_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/footnote.png"/>
</Relationships>
"""
    source = tmp_path / "input.docx"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", package_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", document_rels)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/footnotes.xml", footnotes_xml)
        zf.writestr("word/_rels/footnotes.xml.rels", footnotes_rels)
        zf.writestr("word/media/image1.png", image_buffer.getvalue())
        zf.writestr("word/media/footnote.png", footnote_image_buffer.getvalue())

    output = tmp_path / "decomposed"
    output.mkdir()
    current_view = tmp_path / "current-view.docx"
    normalization_warnings = mod._write_current_view_docx(source, current_view)
    figures, warnings = mod.inspect_docx(current_view, output)

    assert normalization_warnings == []
    assert warnings == []
    assert len(figures) == 5
    assert figures[0]["source_part"] == figures[1]["source_part"]
    assert figures[0]["label"] == "Visible architecture"
    assert figures[0]["revision_state"] == "current"
    assert figures[0]["include_in_current_view"] is True
    assert figures[0]["display_mode"] == "cropped"
    assert figures[0]["block_order"] == 2
    assert figures[0]["context"]["heading_path"] == ["Section"]
    assert figures[0]["context"]["current"] == "Visible crop"
    with Image.open(output / figures[0]["visible_path"]) as visible:
        assert visible.size == (50, 40)

    assert figures[1]["include_in_current_view"] is True
    assert figures[1]["fallback"] == "page_required"
    assert figures[1]["fallback_reasons"] == ["rotation"]
    assert figures[1]["block_order"] == 3
    assert figures[1]["visible_path"] is None
    assert figures[2]["context"]["current"] == "Alternate image selected text"
    assert figures[2]["block_order"] == 4
    assert figures[2]["visible_path"] == "figures/figure-003.png"
    assert figures[2]["fallback"] is None
    assert figures[3]["revision_state"] == "deleted"
    assert figures[3]["include_in_current_view"] is False
    assert figures[3]["excluded_reason"] == "revision_not_in_current_view"
    assert figures[3]["visible_path"] is None
    assert figures[3]["fallback"] is None
    assert not (output / "figures" / "figure-004.png").exists()
    assert figures[4]["part"] == "word/footnotes.xml"
    assert figures[4]["part_role"] == "footnote"
    assert figures[4]["include_in_current_view"] is True
    assert figures[4]["visible_path"] == "figures/figure-005.png"
    assert figures[4]["block_order"] is None
    assert figures[4]["likely_decorative"] is False

    if shutil.which("pandoc"):
        full_output = tmp_path / "full-decomposition"
        manifest = mod.decompose_docx(source, full_output)
        markdown = (full_output / "document.md").read_text(encoding="utf-8")
        tables = manifest["tables"]

        assert manifest["schema_version"] == 3
        assert "blocks_path" not in manifest
        expected_counts = {
            "figures": 5,
            "current_figures": 4,
            "available_figures": 3,
            "page_fallback_figures": 1,
            "excluded_revision_figures": 1,
        }
        assert {
            key: value
            for key, value in manifest["counts"].items()
            if key != "tables"
        } == expected_counts
        # Pandoc 3.1 and 3.9 differ on whether a nested table gets its own
        # callback. The catalog is explicitly best-effort, not a table count.
        assert len(tables) in {5, 6}
        assert manifest["counts"]["tables"] == len(tables)
        assert [table["id"] for table in tables] == [
            f"table-{index:03d}" for index in range(1, len(tables) + 1)
        ]
        assert all(table["content_source"] == "document.md" for table in tables)
        assert all(table["heading_path"] == ["Section"] for table in tables)
        assert tables[0]["label"] == "MCE Choice catalog"
        assert set(tables[0]) == {
            "id", "source_id", "label", "order", "heading_path",
            "content_source",
        }
        assert "Merged cell" in markdown
        assert "Nested A" in markdown
        assert "Content control table" in markdown
        assert "Alternate image selected text" in markdown
        assert "fallback text" not in markdown
        assert "MCE Choice table" in markdown
        assert "MCE fallback table" not in markdown
        assert not (full_output / "blocks.jsonl").exists()
        assert not (full_output / "content.jsonl").exists()
        assert not (full_output / "tables").exists()
        assert not (full_output / "document.ast.json").exists()
        assert not (full_output / ".table-catalog.jsonl").exists()
        assert not (full_output / ".current-view.docx").exists()
        assert not (full_output / "raw-media").exists()

        catalog_path = tmp_path / "small-table-catalog.jsonl"
        catalog_path.write_text(json.dumps({
            "source_id": "source-table",
            "label": "Results",
            "order": 2,
            "heading_path": ["Analysis"],
        }) + "\n", encoding="utf-8")
        named_tables = mod._read_table_catalog(catalog_path)
        assert named_tables == [{
            "id": "table-001",
            "source_id": "source-table",
            "label": "Results",
            "order": 2,
            "heading_path": ["Analysis"],
            "content_source": "document.md",
        }]
        original_catalog_limit = mod.MAX_TABLE_CATALOG_BYTES
        mod.MAX_TABLE_CATALOG_BYTES = 1
        try:
            try:
                mod._read_table_catalog(catalog_path)
            except mod.ResourceLimitError:
                pass
            else:
                raise AssertionError("table catalog size limit must fail loudly")
        finally:
            mod.MAX_TABLE_CATALOG_BYTES = original_catalog_limit

    limited_output = tmp_path / "limited"
    limited_output.mkdir()
    original_pixel_limit = mod.MAX_IMAGE_PIXELS
    original_exif_transpose = mod.ImageOps.exif_transpose
    mod.MAX_IMAGE_PIXELS = 10
    mod.ImageOps.exif_transpose = lambda image: (_ for _ in ()).throw(
        AssertionError("pixel limit must run before EXIF transpose/decode")
    )
    try:
        try:
            mod.inspect_docx(current_view, limited_output)
        except mod.ResourceLimitError:
            pass
        else:
            raise AssertionError("image pixel limit must fail loudly")
    finally:
        mod.MAX_IMAGE_PIXELS = original_pixel_limit
        mod.ImageOps.exif_transpose = original_exif_transpose

    image_free_output = tmp_path / "image-free"
    image_free_output.mkdir()
    image_free_root = mod._xml(
        f'<w:styles xmlns:w="{mod.W_NS}"><w:p><w:r><w:t>x</w:t></w:r></w:p></w:styles>'.encode()
    )
    original_paragraph_context = mod._paragraph_context
    mod._paragraph_context = lambda root: (_ for _ in ()).throw(
        AssertionError("image-free parts must skip paragraph context")
    )
    try:
        with zipfile.ZipFile(source) as zf:
            assert mod._inspect_figures(
                zf,
                [("word/styles.xml", "other", image_free_root)],
                image_free_output,
                [],
            ) == []
    finally:
        mod._paragraph_context = original_paragraph_context


def test_docx_mce_fallback_becomes_the_only_current_view():
    pytest.importorskip("PIL.Image")
    pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/decompose_docx.py")

    root = mod._xml(f"""
<w:document xmlns:w="{mod.W_NS}" xmlns:r="{mod.R_NS}"
 xmlns:v="{mod.V_NS}" xmlns:mc="{mod.MC_NS}"
 xmlns:future="urn:example:unsupported"
 xmlns:o="urn:schemas-microsoft-com:office:office">
  <w:body><w:p><w:r><w:t>Before</w:t></w:r>
    <mc:AlternateContent>
      <mc:Choice Requires="future">
        <w:r><w:t>future text</w:t><w:object><o:OLEObject/></w:object></w:r>
      </mc:Choice>
      <mc:Fallback>
        <w:r><w:t>fallback text</w:t><w:pict><v:shape>
          <v:imagedata r:id="rId1"/>
        </v:shape></w:pict></w:r>
      </mc:Fallback>
    </mc:AlternateContent>
  </w:p></w:body>
</w:document>
""".encode())

    assert mod._select_alternate_content(root) == []
    assert root.xpath(".//mc:AlternateContent", namespaces=mod.NS) == []
    assert mod._unsupported_content_warnings(root) == []
    paragraphs = mod._paragraph_context(root)
    paragraph = root.xpath(".//w:p", namespaces=mod.NS)[0]
    assert paragraphs[paragraph]["text"] == "Beforefallback text"
    candidates = mod._image_candidates(root)
    assert len(candidates) == 1
    assert candidates[0].get(f"{{{mod.R_NS}}}id") == "rId1"

    unavailable = mod._xml(f"""
<w:document xmlns:w="{mod.W_NS}" xmlns:mc="{mod.MC_NS}"
 xmlns:future="urn:example:unsupported">
  <w:body><w:p><mc:AlternateContent>
    <mc:Choice Requires="future"><w:r><w:t>future only</w:t></w:r></mc:Choice>
  </mc:AlternateContent></w:p></w:body>
</w:document>
""".encode())
    assert mod._select_alternate_content(unavailable) == [
        "AlternateContent has no supported Choice or Fallback; "
        "page rendering may be required"
    ]
    assert unavailable.xpath(".//mc:AlternateContent", namespaces=mod.NS) == []


def test_docx_package_normalization_preserves_warnings_and_source(tmp_path):
    pytest.importorskip("PIL.Image")
    pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/decompose_docx.py")

    document_xml = f"""
<w:document xmlns:w="{mod.W_NS}" xmlns:mc="{mod.MC_NS}"
 xmlns:future="urn:example:unsupported">
  <w:body><w:p><mc:AlternateContent>
    <mc:Choice Requires="future"><w:r><w:t>future only</w:t></w:r></mc:Choice>
  </mc:AlternateContent></w:p></w:body>
</w:document>
""".encode()
    source = tmp_path / "source.docx"
    current_view = tmp_path / "current-view.docx"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("word/document.xml", document_xml)

    assert mod._write_current_view_docx(source, current_view) == [
        "word/document.xml: AlternateContent has no supported Choice or "
        "Fallback; page rendering may be required"
    ]
    with zipfile.ZipFile(source) as zf:
        source_root = mod._xml(zf.read("word/document.xml"))
    with zipfile.ZipFile(current_view) as zf:
        current_root = mod._xml(zf.read("word/document.xml"))
    assert source_root.xpath(".//mc:AlternateContent", namespaces=mod.NS)
    assert not current_root.xpath(".//mc:AlternateContent", namespaces=mod.NS)


def test_docx_mce_prunes_only_relationships_from_discarded_branches(tmp_path):
    pytest.importorskip("PIL.Image")
    pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/decompose_docx.py")

    document_xml = f"""
<w:document xmlns:w="{mod.W_NS}" xmlns:r="{mod.R_NS}"
 xmlns:mc="{mod.MC_NS}" xmlns:wps="{mod.WPS_NS}">
  <w:body><w:p><w:r><w:footnoteReference w:id="1"/></w:r></w:p><w:sectPr>
    <mc:AlternateContent>
      <mc:Choice Requires="wps">
        <w:headerReference w:type="default" r:id="rIdH1"/>
      </mc:Choice>
      <mc:Fallback>
        <w:headerReference w:type="default" r:id="rIdH2"/>
      </mc:Fallback>
    </mc:AlternateContent>
  </w:sectPr></w:body>
</w:document>
""".encode()
    document_rels = f"""
<Relationships xmlns="{mod.PKG_REL_NS}">
  <Relationship Id="rIdH1" Type="urn:relationships/header"
    Target="header1.xml"/>
  <Relationship Id="rIdH2" Type="urn:relationships/header"
    Target="header2.xml"/>
  <Relationship Id="rIdStyles" Type="urn:relationships/styles"
    Target="styles.xml"/>
  <Relationship Id="rIdFootnotes" Type="urn:relationships/footnotes"
    Target="footnotes.xml"/>
</Relationships>
""".encode()
    header1_xml = f'<w:hdr xmlns:w="{mod.W_NS}"><w:p/></w:hdr>'
    header2_xml = f"""
<w:hdr xmlns:w="{mod.W_NS}"
 xmlns:o="urn:schemas-microsoft-com:office:office">
  <w:p><w:r><w:object><o:OLEObject/></w:object></w:r></w:p>
</w:hdr>
"""
    source = tmp_path / "source.docx"
    current_view = tmp_path / "current-view.docx"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/_rels/document.xml.rels", document_rels)
        zf.writestr("word/header1.xml", header1_xml)
        zf.writestr("word/header2.xml", header2_xml)
        zf.writestr("word/styles.xml", f'<w:styles xmlns:w="{mod.W_NS}"/>')
        zf.writestr(
            "word/footnotes.xml",
            f'<w:footnotes xmlns:w="{mod.W_NS}"/>',
        )

    assert mod._write_current_view_docx(source, current_view) == []
    with zipfile.ZipFile(current_view) as zf:
        rels = mod._xml(zf.read("word/_rels/document.xml.rels"))
        rel_ids = {
            rel.get("Id")
            for rel in rels.findall(f"{{{mod.PKG_REL_NS}}}Relationship")
        }
        discovered = {part for part, _ in mod._discover_parts(zf)}
        assert "word/header2.xml" in zf.namelist()
    assert rel_ids == {"rIdH1", "rIdStyles", "rIdFootnotes"}
    assert "word/header1.xml" in discovered
    assert "word/header2.xml" not in discovered
    assert "word/styles.xml" in discovered
    assert "word/footnotes.xml" in discovered

    output = tmp_path / "decomposed"
    output.mkdir()
    figures, warnings = mod.inspect_docx(current_view, output)
    assert figures == []
    assert warnings == []


def test_docx_decompose_preflights_before_pandoc(tmp_path):
    pytest.importorskip("PIL.Image")
    pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/decompose_docx.py")

    source = tmp_path / "oversized.docx"
    with zipfile.ZipFile(source, "w") as zf:
        zf.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{mod.W_NS}"><w:body><w:p><w:r>'
            '<w:t>x</w:t></w:r></w:p></w:body></w:document>',
        )

    called = False
    original_run_pandoc = mod._run_pandoc
    original_element_limit = mod.MAX_WORD_XML_ELEMENTS

    def unexpected_pandoc(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("package preflight must run before Pandoc")

    mod._run_pandoc = unexpected_pandoc
    mod.MAX_WORD_XML_ELEMENTS = 4
    try:
        try:
            mod.decompose_docx(source, tmp_path / "output")
        except mod.ResourceLimitError as exc:
            assert "Word XML element count" in str(exc)
        else:
            raise AssertionError("over-complex package must fail loudly")
    finally:
        mod._run_pandoc = original_run_pandoc
        mod.MAX_WORD_XML_ELEMENTS = original_element_limit

    assert called is False
    assert not (tmp_path / "output").exists()


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


def test_docx_apply_redline_plan_is_atomic(tmp_path):
    docx = pytest.importorskip("docx")
    etree = pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/apply_redline.py")

    src = tmp_path / "in.docx"
    out = tmp_path / "out.docx"
    document = docx.Document()
    document.add_paragraph("Alpha old; Beta remove; Gamma anchor.")
    document.save(src)

    summary = mod.apply_plan(
        src,
        out,
        author="Review",
        changes=[
            {"op": "replace", "find": "old", "replace": "new", "expect": 1},
            {"op": "delete", "find": "remove", "expect": 1},
            {"op": "insert_after", "find": "anchor", "text": " added", "expect": 1},
        ],
    )
    assert summary["total_changes"] == 3
    with zipfile.ZipFile(out) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    assert root.xpath(".//w:del//w:delText[text()='old']", namespaces=ns)
    assert root.xpath(".//w:ins//w:t[text()='new']", namespaces=ns)
    assert root.xpath(".//w:del//w:delText[text()='remove']", namespaces=ns)
    assert root.xpath(".//w:ins//w:t[text()=' added']", namespaces=ns)

    failed = tmp_path / "must-not-exist.docx"
    with pytest.raises(mod.RedlineError, match="expected 1 editable match"):
        mod.apply_plan(
            src,
            failed,
            author="Review",
            changes=[
                {"op": "replace", "find": "old", "replace": "new", "expect": 1},
                {"op": "delete", "find": "missing", "expect": 1},
            ],
        )
    assert not failed.exists()


def test_docx_apply_redline_auto_matches_typo_uniquely(tmp_path):
    docx = pytest.importorskip("docx")
    etree = pytest.importorskip("lxml.etree")
    mod = _load_skill_script("docx", "scripts/apply_redline.py")

    src = tmp_path / "in.docx"
    out = tmp_path / "out.docx"
    document = docx.Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("关于人工智能")
    paragraph.add_run("技术的详细介绍。")
    document.save(src)

    summary = mod.apply_plan(
        src,
        out,
        author="Review",
        changes=[{
            "op": "replace",
            "find": "关于人工智能枝术的详细介绍",
            "replace": "已更新",
            "expect": 1,
            "match": "auto",
        }],
    )
    assert summary["changes"][0]["match_type"] == "fuzzy"
    with zipfile.ZipFile(out) as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    deleted = "".join(root.xpath(".//w:del//w:delText/text()", namespaces=ns))
    assert deleted == "关于人工智能技术的详细介绍"
    assert root.xpath(".//w:ins//w:t[text()='已更新']", namespaces=ns)


def test_docx_apply_redline_auto_rejects_duplicate_segments(tmp_path):
    docx = pytest.importorskip("docx")
    mod = _load_skill_script("docx", "scripts/apply_redline.py")

    src = tmp_path / "in.docx"
    out = tmp_path / "must-not-exist.docx"
    document = docx.Document()
    document.add_paragraph("Repeated unique-looking target")
    document.add_paragraph("Repeated unique-looking target")
    document.save(src)

    with pytest.raises(mod.RedlineError, match="appears multiple times"):
        mod.apply_plan(
            src,
            out,
            author="Review",
            changes=[{
                "op": "replace",
                "find": "Repeated unique-looking target",
                "replace": "new",
                "expect": 1,
            }],
        )
    assert not out.exists()


def test_docx_apply_redline_does_not_bridge_hyperlinks(tmp_path):
    docx = pytest.importorskip("docx")
    oxml = pytest.importorskip("docx.oxml")
    ns = pytest.importorskip("docx.oxml.ns")
    mod = _load_skill_script("docx", "scripts/apply_redline.py")

    prefix = "This is a long editable prefix with enough context before the "
    suffix = " and enough editable context after the unsupported node."
    src = tmp_path / "hyperlink.docx"
    out = tmp_path / "must-not-exist.docx"
    document = docx.Document()
    paragraph = document.add_paragraph()
    paragraph.add_run(prefix)
    hyperlink = oxml.OxmlElement("w:hyperlink")
    hyperlink.set(ns.qn("r:id"), "rId999")
    run = oxml.OxmlElement("w:r")
    text = oxml.OxmlElement("w:t")
    text.text = "LINK"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    paragraph.add_run(suffix)
    document.save(src)

    with pytest.raises(mod.RedlineError, match="matching failed"):
        mod.apply_plan(
            src,
            out,
            author="Review",
            changes=[{
                "op": "replace",
                "find": prefix + "LINK" + suffix,
                "replace": "REPLACED",
                "expect": 1,
                "match": "auto",
            }],
        )
    assert not out.exists()


def test_docx_default_reference_does_not_leak_template_media(tmp_path):
    pandoc = shutil.which("pandoc")
    if not pandoc:
        pytest.skip("pandoc not installed")

    reference_doc = SRC_DIR / "docx" / "references" / "reference.docx"
    assert _word_media_members(reference_doc) == []

    plain_md = tmp_path / "plain.md"
    plain_md.write_text("# Hello\n\nPlain paragraph.\n", encoding="utf-8")
    plain_out = tmp_path / "plain.docx"
    subprocess.run(
        [
            pandoc,
            str(plain_md),
            f"--reference-doc={reference_doc}",
            "-o",
            str(plain_out),
        ],
        check=True,
    )
    assert _word_media_members(plain_out) == []

    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8"
        "AAwMCAO+/p9sAAAAASUVORK5CYII="
    )
    (tmp_path / "dot.png").write_bytes(tiny_png)
    image_md = tmp_path / "with-image.md"
    image_md.write_text("# Hello\n\n![dot](dot.png)\n", encoding="utf-8")
    image_out = tmp_path / "with-image.docx"
    subprocess.run(
        [
            pandoc,
            str(image_md),
            f"--reference-doc={reference_doc}",
            "-o",
            str(image_out),
        ],
        cwd=tmp_path,
        check=True,
    )
    assert _word_media_members(image_out)


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


def test_pptx_replace_text_auto_and_ambiguity_are_atomic(tmp_path):
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    mod = _load_skill_script("pptx", "scripts/replace_text.py")

    src = tmp_path / "in.pptx"
    out = tmp_path / "out.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        util.Inches(1), util.Inches(1), util.Inches(6), util.Inches(1)
    )
    paragraph = box.text_frame.paragraphs[0]
    paragraph.add_run().text = "关于人工智能"
    paragraph.add_run().text = "技术的详细介绍"
    prs.save(src)

    summary = mod.replace_text(
        src,
        out,
        [{
            "find": "关于人工智能枝术的详细介绍",
            "replace": "已更新",
            "expect": 1,
            "match": "auto",
        }],
        slides=None,
        allow_missing=False,
    )
    assert summary["replacements"][0]["match_type"] == "fuzzy"
    assert summary["paragraph_rewrites"] == 1
    reopened = pptx.Presentation(str(out))
    assert reopened.slides[0].shapes[0].text == "已更新"

    duplicate_src = tmp_path / "duplicate.pptx"
    duplicate_out = tmp_path / "must-not-exist.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for top in (1, 2):
        shape = slide.shapes.add_textbox(
            util.Inches(1), util.Inches(top), util.Inches(6), util.Inches(1)
        )
        shape.text = "Repeated unique-looking target"
    prs.save(duplicate_src)
    with pytest.raises(ValueError, match="multiple times"):
        mod.replace_text(
            duplicate_src,
            duplicate_out,
            [{
                "find": "Repeated unique-looking target",
                "replace": "new",
                "expect": 1,
            }],
            slides=None,
            allow_missing=False,
        )
    assert not duplicate_out.exists()


def test_pptx_replace_text_does_not_bridge_fields(tmp_path):
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    xmlchemy = pytest.importorskip("pptx.oxml.xmlchemy")
    mod = _load_skill_script("pptx", "scripts/replace_text.py")

    prefix = "This is a long editable prefix with enough context before field "
    suffix = " and enough editable context after the unsupported field node."
    src = tmp_path / "field.pptx"
    out = tmp_path / "must-not-exist.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        util.Inches(1), util.Inches(1), util.Inches(8), util.Inches(2)
    )
    paragraph = box.text_frame.paragraphs[0]
    paragraph.add_run().text = prefix
    field = xmlchemy.OxmlElement("a:fld")
    field.set("id", "{00000000-0000-0000-0000-000000000001}")
    field.set("type", "slidenum")
    field.append(xmlchemy.OxmlElement("a:rPr"))
    field_text = xmlchemy.OxmlElement("a:t")
    field_text.text = "42"
    field.append(field_text)
    paragraph._p.append(field)
    paragraph.add_run().text = suffix
    prs.save(src)

    with pytest.raises(ValueError, match="expected 1 editable match"):
        mod.replace_text(
            src,
            out,
            [{
                "find": prefix + "42" + suffix,
                "replace": "REPLACED",
                "expect": 1,
                "match": "auto",
            }],
            slides=None,
            allow_missing=False,
        )
    assert not out.exists()


def test_pptx_slide_range_rejects_huge_materialization():
    mod = _load_skill_script("pptx", "scripts/replace_text.py")
    with pytest.raises(SystemExit, match="select at most"):
        mod._parse_slides("1-999999999")


def test_pptx_exact_multiple_matches_in_one_run_group(tmp_path):
    pptx = pytest.importorskip("pptx")
    util = pytest.importorskip("pptx.util")
    mod = _load_skill_script("pptx", "scripts/replace_text.py")

    src = tmp_path / "multiple.pptx"
    out = tmp_path / "multiple-out.pptx"
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(
        util.Inches(1), util.Inches(1), util.Inches(6), util.Inches(1)
    )
    box.text = "old middle old"
    prs.save(src)

    summary = mod.replace_text(
        src,
        out,
        [{"find": "old", "replace": "NEW", "expect": 2, "match": "exact"}],
        slides=None,
        allow_missing=False,
    )
    assert summary["total_hits"] == 2
    reopened = pptx.Presentation(str(out))
    assert reopened.slides[0].shapes[0].text == "NEW middle NEW"
