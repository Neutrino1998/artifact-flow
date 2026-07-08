#!/usr/bin/env python3
"""把技能目录打包成可导入的 zip,并做导入前的预检(仅标准库)。

用法:
    python package_skill.py 技能目录/ [输出.zip]

预检不是权威校验 —— 权威硬门槛在平台导入时执行(会返回逐条 findings);
这里只拦最常见的低级错,省一轮下载/上传往返:
  - 根目录有 SKILL.md,frontmatter 有 ---...--- 围栏
  - frontmatter 里有 name: 与 description:
  - 剥 frontmatter 后正文非空
  - zip 总大小 ≤100MB(平台单包上限)

排除打包:__pycache__ / *.pyc / .DS_Store / .git。
"""

import sys
import zipfile
from pathlib import Path

MAX_ZIP_BYTES = 100 * 1024 * 1024
_JUNK = {"__pycache__", ".DS_Store", ".git"}


def _preflight(skill_dir: Path) -> str:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        raise SystemExit(f"error: {md} 不存在 —— SKILL.md 必须在技能目录根部")
    text = md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise SystemExit("error: SKILL.md 必须以 --- 开头的 YAML frontmatter 开始")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SystemExit("error: frontmatter 没有闭合的 --- 围栏")
    fm, body = parts[1], parts[2]
    for key in ("name:", "description:"):
        if key not in fm:
            raise SystemExit(f"error: frontmatter 缺 {key.rstrip(':')} 字段")
    if not body.strip():
        raise SystemExit("error: 剥掉 frontmatter 后正文为空 —— 平台会硬拒(md.body_empty)")
    name = next(
        (ln.split(":", 1)[1].strip().strip("\"'") for ln in fm.splitlines()
         if ln.strip().startswith("name:")), "skill")
    return name


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit(__doc__)
    skill_dir = Path(sys.argv[1]).resolve()
    name = _preflight(skill_dir)
    out = Path(sys.argv[2]) if len(sys.argv) == 3 else Path(f"{name}.zip")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(skill_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(skill_dir)
            if set(rel.parts) & _JUNK or p.suffix in (".pyc", ".pyo"):
                continue
            zf.write(p, f"{name}/{rel.as_posix()}")

    size = out.stat().st_size
    if size > MAX_ZIP_BYTES:
        raise SystemExit(f"error: {out} 有 {size/2**20:.0f}MB,超过平台 100MB 单包上限 —— "
                         "检查是否把数据/产物打了进去")
    print(f"packaged {out} ({size:,} bytes)")
    print("下一步:persist 这个 zip 为 artifact → 用户下载 → 前端「技能管理 → 导入技能」上传。")


if __name__ == "__main__":
    main()
