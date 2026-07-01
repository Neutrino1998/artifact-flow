"""SkillManager 用户侧列举 + 个人 toggle(C-3,DB)。

覆盖:list 只列可见 + 有效启用态(覆盖后)、toggle 写 user_skill、不可见 → 404、
default 与覆盖态区分(is_overridden)。可见性口径复用 EffectiveSkillSet(单点闸)。
"""

import pytest

from core.skill_manager import SkillManager, SkillNotFoundError
from db.models import Department, DepartmentSkillRule, Skill, User, UserSkill


def _skill(slug, visibility="public", default_enabled=True):
    return Skill(slug=slug, name=slug, description="d", visibility=visibility,
                 default_enabled=default_enabled, source="seeded", skill_md="body")


async def _user(session, uid="u1", dept=None):
    if dept:
        session.add(Department(id=dept, name=dept))
        await session.flush()
    session.add(User(id=uid, username=uid, hashed_password="x", department_id=dept))
    await session.flush()


async def test_list_only_visible_with_effective_state(db_session):
    await _user(db_session)
    db_session.add(_skill("pub", default_enabled=True))
    db_session.add(_skill("off", default_enabled=False))
    db_session.add(_skill("priv", visibility="private"))  # 非 owner → 不可见
    await db_session.flush()

    items = {i["slug"]: i for i in await SkillManager(db_session).list_for_user("u1")}
    assert set(items) == {"pub", "off"}          # private 不列
    assert items["pub"]["enabled"] is True        # default_enabled=True
    assert items["off"]["enabled"] is False       # default_enabled=False
    assert items["pub"]["is_overridden"] is False


async def test_toggle_writes_override_and_flips_enabled(db_session):
    await _user(db_session)
    db_session.add(_skill("pub", default_enabled=True))
    await db_session.flush()

    mgr = SkillManager(db_session)
    out = await mgr.set_enabled("u1", "pub", False)
    assert out["enabled"] is False and out["is_overridden"] is True

    # 持久化:重列反映覆盖态
    items = {i["slug"]: i for i in await mgr.list_for_user("u1")}
    assert items["pub"]["enabled"] is False
    assert items["pub"]["is_overridden"] is True
    assert items["pub"]["default_enabled"] is True   # 系统默认不变


async def test_toggle_upserts_existing_row(db_session):
    await _user(db_session)
    db_session.add(_skill("pub", default_enabled=True))
    db_session.add(UserSkill(user_id="u1", skill_slug="pub", enabled=False))
    await db_session.flush()

    out = await SkillManager(db_session).set_enabled("u1", "pub", True)
    assert out["enabled"] is True


async def test_toggle_invisible_skill_404(db_session):
    await _user(db_session)
    db_session.add(_skill("priv", visibility="private"))  # owner 非 u1
    await db_session.flush()

    with pytest.raises(SkillNotFoundError):
        await SkillManager(db_session).set_enabled("u1", "priv", True)


async def test_toggle_unknown_skill_404(db_session):
    await _user(db_session)
    with pytest.raises(SkillNotFoundError):
        await SkillManager(db_session).set_enabled("u1", "ghost", True)


async def test_disabled_skill_still_visible_for_activation(db_session):
    """关掉的 skill 仍在 list(可见=正确性)—— 前端据此仍可显式激活。"""
    await _user(db_session)
    db_session.add(_skill("pub", default_enabled=True))
    await db_session.flush()
    mgr = SkillManager(db_session)
    await mgr.set_enabled("u1", "pub", False)
    slugs = {i["slug"] for i in await mgr.list_for_user("u1")}
    assert "pub" in slugs
