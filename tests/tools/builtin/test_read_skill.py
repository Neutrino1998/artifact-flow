"""ReadSkillTool 单测(C-2,L2)。

覆盖:可见 slug → 正文 + mount 提示 + activated_skill metadata;不可见 → 404 风格;
无正文 → 错误;空 slug → 错误;契约镜像 read_artifact(AUTO + max_result_size=inf)。
"""

import math

import pytest

from core.effective_skillset import EffectiveSkillSet
from reconcile.snapshot import SkillInfo
from tools.base import ToolPermission
from tools.builtin.read_skill import ReadSkillTool, create_skill_tools


class _FakeService:
    def __init__(self, bodies):
        self._bodies = bodies

    async def get_skill_md(self, slug):
        return self._bodies.get(slug)


def _skillset(*slugs):
    visible = {
        s: SkillInfo(slug=s, name=s, description="", visibility="public",
                     default_enabled=True, owner_user_id=None, allowed_tools=[])
        for s in slugs
    }
    return EffectiveSkillSet(visible=visible, enabled=set(slugs))


def _tool(bodies, *visible):
    return ReadSkillTool(_FakeService(bodies), _skillset(*visible))


def test_contract_mirrors_read_artifact():
    t = _tool({}, )
    assert t.permission == ToolPermission.AUTO
    assert t.max_result_size_chars == math.inf
    assert t.name == "read_skill"


async def test_visible_returns_body_and_activates():
    t = _tool({"a": "GUIDANCE BODY"}, "a")
    res = await t.execute(slug="a")
    assert res.success
    assert "GUIDANCE BODY" in res.data
    assert "mount" in res.data.lower()          # mount 提示
    assert res.metadata["activated_skill"] == "a"


async def test_invisible_is_not_found():
    t = _tool({"secret": "x"}, "a")   # secret 存在但不在该用户 visible 集
    res = await t.execute(slug="secret")
    assert not res.success
    assert "not found" in res.error.lower()
    assert res.metadata.get("activated_skill") is None


async def test_visible_but_no_content_errors():
    t = _tool({}, "a")    # a 可见但 service 取不到正文
    res = await t.execute(slug="a")
    assert not res.success
    assert "no content" in res.error.lower()


async def test_empty_slug_errors():
    t = _tool({"a": "x"}, "a")
    res = await t.execute(slug="  ")
    assert not res.success
    assert "slug" in res.error.lower()


def test_create_skill_tools_empty_when_no_visible():
    assert create_skill_tools(_FakeService({}), None) == []
    assert create_skill_tools(_FakeService({}), EffectiveSkillSet()) == []
    tools = create_skill_tools(_FakeService({}), _skillset("a"))
    assert len(tools) == 1 and tools[0].name == "read_skill"
