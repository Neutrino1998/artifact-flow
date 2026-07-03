#!/usr/bin/env python3
"""把 config/skills-src/<slug>/ 源码目录打成 config/skills/<slug>.zip 预装种子。

为什么存在:seeds 对「目录带附属文件」有意 loud-fail 指向 zip(防附属文件被静默丢,
seeds.py::_parse_skill_dir),所以带脚本/资产的预装 skill 必须以 zip 形态进
config/skills/;但 zip 是二进制,git 里不可读不可 diff —— 真相源放
config/skills-src/(可读可审),本脚本产 zip。**改了 skills-src 必须重跑本脚本**,
tests/reconcile/test_preinstalled_skills.py 会对比「重建 == 已提交」抓漂移。

Deterministic:成员按路径排序、时间戳固定 1980-01-01、权限固定 0644、
ZIP_DEFLATED 固定压缩级 —— 同源必产同字节,seed_hash(sha256(bundle))才稳定,
重跑构建不会造成 reconcile 无谓换血。

用法:
    python scripts/build_skill_zips.py            # 全部重建
    python scripts/build_skill_zips.py docx pptx  # 只建指定 slug
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "config" / "skills-src"
OUT_DIR = ROOT / "config" / "skills"

# 打包排除:开发环境垃圾,永不属于 bundle
_JUNK_NAMES = {".DS_Store", "__pycache__", ".gitkeep"}
_JUNK_SUFFIXES = {".pyc", ".pyo"}

_FIXED_DATE = (1980, 1, 1, 0, 0, 0)   # zip 格式的最小合法时间戳
_FIXED_MODE = 0o644                    # 脚本经 `python x.py` 执行,无需 exec 位


def _members(src: Path) -> list[Path]:
    out = []
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        parts = set(p.relative_to(src).parts)
        if parts & _JUNK_NAMES or p.suffix in _JUNK_SUFFIXES:
            continue
        out.append(p)
    return out


def build_zip(slug: str) -> bytes:
    """单个 skill 源码目录 → deterministic zip 字节(wrapper 前缀 = slug)。"""
    src = SRC_DIR / slug
    if not (src / "SKILL.md").is_file():
        raise SystemExit(f"error: {src}/SKILL.md not found")
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in _members(src):
            arcname = f"{slug}/{path.relative_to(src).as_posix()}"
            zi = zipfile.ZipInfo(arcname, date_time=_FIXED_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = _FIXED_MODE << 16
            zf.writestr(zi, path.read_bytes(), compresslevel=9)
    return buf.getvalue()


def main(argv: list[str]) -> None:
    slugs = argv or sorted(
        p.name for p in SRC_DIR.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))
    )
    if not slugs:
        raise SystemExit(f"error: no skill sources under {SRC_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for slug in slugs:
        blob = build_zip(slug)
        out = OUT_DIR / f"{slug}.zip"
        out.write_bytes(blob)
        print(f"built {out.relative_to(ROOT)} ({len(blob):,} bytes)")


if __name__ == "__main__":
    main(sys.argv[1:])
