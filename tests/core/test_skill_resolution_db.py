"""部门祖先链 + SkillRepository + EffectiveSkillSet 端到端(DB,C-2,决策 10)。

覆盖:祖先链(父覆盖子树)、dept 派生方向(public→deny / department→grant)、user 覆盖。
"""

import pytest
from sqlalchemy import select

from core.department_resolver import load_ancestor_ids
from core.effective_skillset import resolve_effective_skillset
from db.models import Department, DepartmentSkillRule, Skill, User, UserSkill
from reconcile.snapshot import load_skill_snapshot
from repositories.skill_repo import SkillRepository


async def _tree(session):
    """root → mid → leaf;user u1 挂 leaf。"""
    session.add(Department(id="root", name="root"))
    await session.flush()
    session.add(Department(id="mid", parent_id="root", name="mid"))
    await session.flush()
    session.add(Department(id="leaf", parent_id="mid", name="leaf"))
    await session.flush()
    session.add(User(id="u1", username="u1", hashed_password="x", department_id="leaf"))
    await session.flush()


def _skill(slug, visibility="public", default_enabled=True):
    return Skill(slug=slug, name=slug, description="", visibility=visibility,
                 default_enabled=default_enabled, source="seeded", skill_md="body")


async def test_ancestor_chain_parent_covers_subtree(db_session):
    await _tree(db_session)
    chain = await load_ancestor_ids(db_session, "leaf")
    assert chain == ["leaf", "mid", "root"]


async def test_no_department_empty_chain(db_session):
    assert await load_ancestor_ids(db_session, None) == []


async def _resolve(db_session, user_id="u1"):
    repo = SkillRepository(db_session)
    snap = await load_skill_snapshot(db_session)
    dept_id = await repo.user_department_id(user_id)
    ancestors = await load_ancestor_ids(db_session, dept_id)
    overrides = await repo.user_overrides(user_id)
    dept_matched = await repo.dept_matched_slugs(ancestors)
    return resolve_effective_skillset(user_id, snap, overrides, dept_matched)


async def test_department_skill_granted_via_ancestor_rule(db_session):
    await _tree(db_session)
    db_session.add(_skill("dept-skill", visibility="department"))
    await db_session.flush()
    # 规则挂在祖先 mid → 覆盖子树 leaf 用户(决策 10 父覆盖)
    db_session.add(DepartmentSkillRule(department_id="mid", skill_slug="dept-skill"))
    await db_session.flush()

    eff = await _resolve(db_session)
    assert "dept-skill" in eff.visible


async def test_department_skill_hidden_without_rule(db_session):
    await _tree(db_session)
    db_session.add(_skill("dept-skill", visibility="department"))
    await db_session.flush()
    eff = await _resolve(db_session)
    assert "dept-skill" not in eff.visible


async def test_public_skill_denied_by_ancestor_rule(db_session):
    await _tree(db_session)
    db_session.add(_skill("pub", visibility="public"))
    await db_session.flush()
    # public + 规则 = deny 例外 → leaf 用户不可见
    db_session.add(DepartmentSkillRule(department_id="root", skill_slug="pub"))
    await db_session.flush()

    eff = await _resolve(db_session)
    assert "pub" not in eff.visible


async def test_user_override_persisted(db_session):
    await _tree(db_session)
    db_session.add(_skill("pub", visibility="public", default_enabled=True))
    await db_session.flush()
    db_session.add(UserSkill(user_id="u1", skill_slug="pub", enabled=False))
    await db_session.flush()

    eff = await _resolve(db_session)
    assert "pub" in eff.visible       # 仍可见(opt-in 合法)
    assert "pub" not in eff.enabled    # 用户关掉 → 不进 L1
