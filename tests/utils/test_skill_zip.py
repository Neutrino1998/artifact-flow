"""utils.skill_zip 单测:定位唯一 SKILL.md + 剥壳前缀(D-1 seed / D-2 mount 共用)。"""

import pytest

from utils.skill_zip import SkillZipError, locate_skill_md, strip_prefix


@pytest.mark.parametrize(
    "names,member,prefix",
    [
        (["SKILL.md", "references/n.md"], "SKILL.md", ""),                  # 裸根
        (["pkg/SKILL.md", "pkg/scripts/x.py"], "pkg/SKILL.md", "pkg"),      # 单层 wrapper
        (["a/b/c/SKILL.md", "a/b/c/refs/n"], "a/b/c/SKILL.md", "a/b/c"),    # 深嵌
    ],
)
def test_locate_and_strip(names, member, prefix):
    assert locate_skill_md(names, "z") == member
    assert strip_prefix(member) == prefix


def test_locate_ignores_dir_entries_and_non_skill():
    names = ["pkg/", "pkg/SKILL.md", "pkg/NOTSKILL.md", "pkg/skill.md"]
    assert locate_skill_md(names, "z") == "pkg/SKILL.md"   # 大小写敏感、目录条目忽略


def test_locate_zero_loud_fails():
    with pytest.raises(SkillZipError, match="no SKILL.md"):
        locate_skill_md(["a.txt", "refs/n.md"], "bundle 'x'")


def test_locate_multiple_loud_fails():
    with pytest.raises(SkillZipError, match="multiple SKILL.md"):
        locate_skill_md(["a/SKILL.md", "b/SKILL.md"], "bundle 'x'")
