"""Skill bundle zip helpers —— 定位 bundle 里唯一的 SKILL.md 成员。

D-1 seed 解析(`reconcile/seeds`)与 D-2 mount(`tools/builtin/read_skill`
的 MountSkillTool)都要在一个 bundle zip 里找到那唯一的 SKILL.md:seed 侧读它的
frontmatter,mount 侧据它的父目录算**剥壳前缀**(解到 `/workspace/.skills/<slug>/`
时把 wrapper 目录去掉)。一个定位器 = 两处不漂移(否则 mount 可能剥错前缀)。

纯函数、无 IO(调用方给 namelist);不依赖 reconcile / tools 任一层(utils 是
最底层,两边都可 import)。
"""

from typing import List


class SkillZipError(Exception):
    """bundle zip 里 SKILL.md 数量非一(0 个 / 多个)。调用方按其语境转
    SeedError(seed)/ ToolResult 失败(mount)。"""


def locate_skill_md(names: List[str], where: str) -> str:
    """返回 zip 里唯一的 SKILL.md 成员名;0 个 / 多个 → SkillZipError。

    裸根 / 单层 wrapper `<name>/SKILL.md` / repo 深层嵌套都吃 —— 唯一以 `SKILL.md`
    结尾(非目录条目)的成员即入口,无论它在哪一层。`where` 只用于错误信息定位。
    """
    md = [n for n in names if not n.endswith("/") and n.rsplit("/", 1)[-1] == "SKILL.md"]
    if not md:
        raise SkillZipError(f"{where} contains no SKILL.md")
    if len(md) > 1:
        raise SkillZipError(
            f"{where} contains multiple SKILL.md ({sorted(md)}); one zip = one skill"
        )
    return md[0]


def strip_prefix(member: str) -> str:
    """剥壳前缀 = SKILL.md 的父目录(裸根 → 空串)。

    解压后把 `<extract>/<strip_prefix>` 整棵移到 `/workspace/.skills/<slug>/`,
    使 SKILL.md 恰落在约定路径顶层。深层嵌套(`repo/skills/x/SKILL.md`)返回
    `repo/skills/x`,mv 叶子目录即可。
    """
    return member.rsplit("/", 1)[0] if "/" in member else ""
