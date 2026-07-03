"""预装 skill 集(E-4)回归 —— 三道闸:

1. **防漂移**:config/skills/<slug>.zip 必须与从 config/skills-src/<slug>/
   重建的结果**内容等价**(manifest:成员路径/时间戳/属性/解压字节)。改了
   skills-src 忘跑 `python scripts/build_skill_zips.py` 时在这里响。
   不比压缩字节 —— DEFLATE 输出依赖 zlib 实现(zlib-ng 机器会假失败)。
2. **过自家硬门**:每个预装 zip 过 E-1 validator 必须零 error 零 warning
   (预装集是"真语料验证",warning 也不许 —— 用户导入才允许 warning 放行)。
3. **seed 解析干净**:parse_skill_seeds 吃下 config/skills/ 全量不抛 SeedError,
   且五个预装 slug 都在、public + default_enabled(用户开箱即在 L1)。
"""

import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

from reconcile.seeds import parse_skill_seeds
from utils.skill_validator import validate_skill_zip

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "config" / "skills-src"
ZIP_DIR = ROOT / "config" / "skills"
PREINSTALLED = ["docx", "pdf", "pptx", "skill-creator", "xlsx"]
# 纯散文预装(SKILL.md-only 目录形态,bundle=NULL,无 zip/构建链)
PREINSTALLED_PROSE = ["html-artifact-design"]


def _build_zip(slug: str) -> bytes:
    spec = importlib.util.spec_from_file_location(
        "build_skill_zips", ROOT / "scripts" / "build_skill_zips.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_zip(slug)


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
        assert (seed.bundle is None) == is_prose, (
            f"{slug}: bundle 形态与预期不符(带脚本的走 zip,纯散文走目录)"
        )
        assert seed.skill_md.strip()
