"""预装 skill 集(E-4)回归 —— 三道闸:

1. **防漂移**:config/skills/<slug>.zip 必须等于从 config/skills-src/<slug>/
   重建的字节(构建是 deterministic 的)。改了 skills-src 忘跑
   `python scripts/build_skill_zips.py` 时在这里响。
2. **过自家硬门**:每个预装 zip 过 E-1 validator 必须零 error 零 warning
   (预装集是"真语料验证",warning 也不许 —— 用户导入才允许 warning 放行)。
3. **seed 解析干净**:parse_skill_seeds 吃下 config/skills/ 全量不抛 SeedError,
   且五个预装 slug 都在、public + default_enabled(用户开箱即在 L1)。
"""

import importlib.util
from pathlib import Path

import pytest

from reconcile.seeds import parse_skill_seeds
from utils.skill_validator import validate_skill_zip

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "config" / "skills-src"
ZIP_DIR = ROOT / "config" / "skills"
PREINSTALLED = ["docx", "pdf", "pptx", "skill-creator", "xlsx"]


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


@pytest.mark.parametrize("slug", PREINSTALLED)
def test_committed_zip_matches_rebuild(slug):
    committed = (ZIP_DIR / f"{slug}.zip").read_bytes()
    assert committed == _build_zip(slug), (
        f"{slug}.zip 与 skills-src 重建结果不一致 —— "
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
    for slug in PREINSTALLED:
        assert slug in by_slug, f"seed 解析缺 {slug}"
        seed = by_slug[slug]
        assert seed.visibility == "public"
        assert seed.default_enabled is True
        assert seed.bundle is not None, "预装 skill 应为 bundle 形态(带脚本)"
        assert seed.skill_md.strip()
