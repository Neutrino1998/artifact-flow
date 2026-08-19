"""Admin department access API tests.

Coverage: auth, effective display, direct/inherited rules, idempotent rule
mutation, private skill rejection, and missing department/resource mapping.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.management.department_access_manager import DepartmentAccessManager
from db.models import (
    Department,
    DepartmentSkillRule,
    DepartmentUnitRule,
    Skill,
    ToolUnit,
)

pytestmark = pytest.mark.asyncio


async def _seed_departments(db_session):
    root = Department(id="dept-root", name="Root")
    leaf = Department(id="dept-leaf", name="Leaf", parent_id="dept-root")
    db_session.add(root)
    await db_session.flush()
    db_session.add(leaf)
    await db_session.commit()
    return root, leaf


def _skill(slug: str, visibility: str = "public", **kw) -> Skill:
    return Skill(
        id=kw.get("id", slug),
        slug=slug,
        namespace_key=kw.get("owner_user_id") or "",
        name=kw.get("name", slug),
        description=kw.get("description", ""),
        visibility=visibility,
        default_enabled=kw.get("default_enabled", True),
        owner_user_id=kw.get("owner_user_id"),
        source=kw.get("source", "dynamic"),
        skill_md="body",
        bundle=b"skill-zip",
    )


def _unit(name: str, visibility: str = "public", kind: str = "tool") -> ToolUnit:
    return ToolUnit(
        name=name,
        kind=kind,
        description=f"{name} unit",
        visibility=visibility,
        source="dynamic",
    )


async def _seed_resources(db_session):
    db_session.add_all([
        _skill("public-skill", "public"),
        _skill("dept-skill", "department"),
        _skill("private-skill", "private"),
        _unit("public_unit", "public", kind="tool"),
        _unit("dept_mcp", "department", kind="mcp"),
    ])
    await db_session.commit()


class TestAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/admin/department-access/dept-root")
        assert resp.status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        resp = await client.get("/api/v1/admin/department-access/dept-root")
        assert resp.status_code == 403


class TestDepartmentAccessRead:
    async def test_get_effective_access_with_direct_and_inherited_rules(
        self, admin_client: AsyncClient, db_session
    ):
        await _seed_departments(db_session)
        await _seed_resources(db_session)
        db_session.add_all([
            DepartmentSkillRule(
                department_id="dept-root", skill_id="public-skill"
            ),
            DepartmentSkillRule(
                department_id="dept-leaf", skill_id="dept-skill"
            ),
            DepartmentUnitRule(
                department_id="dept-root", unit_name="public_unit"
            ),
            DepartmentUnitRule(
                department_id="dept-leaf", unit_name="dept_mcp"
            ),
        ])
        await db_session.commit()

        resp = await admin_client.get("/api/v1/admin/department-access/dept-leaf")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["department"]["id"] == "dept-leaf"

        skills = {s["slug"]: s for s in body["skills"]}
        assert "private-skill" not in skills
        assert skills["public-skill"]["rule_action"] == "deny"
        assert skills["public-skill"]["direct_rule"] is False
        assert skills["public-skill"]["inherited_rule"] == {
            "department_id": "dept-root",
            "department_name": "Root",
        }
        assert skills["public-skill"]["effective_allowed"] is False
        assert skills["dept-skill"]["rule_action"] == "grant"
        assert skills["dept-skill"]["direct_rule"] is True
        assert skills["dept-skill"]["inherited_rule"] is None
        assert skills["dept-skill"]["effective_allowed"] is True

        units = {u["name"]: u for u in body["units"]}
        assert units["public_unit"]["rule_action"] == "deny"
        assert units["public_unit"]["effective_allowed"] is False
        assert units["public_unit"]["inherited_rule"]["department_id"] == "dept-root"
        assert units["dept_mcp"]["rule_action"] == "grant"
        assert units["dept_mcp"]["direct_rule"] is True
        assert units["dept_mcp"]["effective_allowed"] is True

    async def test_get_missing_department_404(self, admin_client: AsyncClient):
        resp = await admin_client.get("/api/v1/admin/department-access/nope")
        assert resp.status_code == 404


class TestDepartmentAccessMutation:
    async def test_put_and_delete_skill_rule_are_idempotent(
        self, admin_client: AsyncClient, db_session
    ):
        await _seed_departments(db_session)
        db_session.add(_skill("public-skill", "public"))
        await db_session.commit()

        for _ in range(2):
            resp = await admin_client.put(
                "/api/v1/admin/department-access/dept-leaf/skills/public-skill"
            )
            assert resp.status_code == 204, resp.text

        get_resp = await admin_client.get("/api/v1/admin/department-access/dept-leaf")
        item = {s["slug"]: s for s in get_resp.json()["skills"]}["public-skill"]
        assert item["direct_rule"] is True
        assert item["effective_allowed"] is False

        for _ in range(2):
            resp = await admin_client.delete(
                "/api/v1/admin/department-access/dept-leaf/skills/public-skill"
            )
            assert resp.status_code == 204, resp.text

        get_resp = await admin_client.get("/api/v1/admin/department-access/dept-leaf")
        item = {s["slug"]: s for s in get_resp.json()["skills"]}["public-skill"]
        assert item["direct_rule"] is False
        assert item["effective_allowed"] is True

    async def test_put_and_delete_unit_rule_are_idempotent(
        self, admin_client: AsyncClient, db_session
    ):
        await _seed_departments(db_session)
        db_session.add(_unit("dept_mcp", "department", kind="mcp"))
        await db_session.commit()

        for _ in range(2):
            resp = await admin_client.put(
                "/api/v1/admin/department-access/dept-leaf/units/dept_mcp"
            )
            assert resp.status_code == 204, resp.text

        get_resp = await admin_client.get("/api/v1/admin/department-access/dept-leaf")
        item = {u["name"]: u for u in get_resp.json()["units"]}["dept_mcp"]
        assert item["direct_rule"] is True
        assert item["effective_allowed"] is True

        for _ in range(2):
            resp = await admin_client.delete(
                "/api/v1/admin/department-access/dept-leaf/units/dept_mcp"
            )
            assert resp.status_code == 204, resp.text

        get_resp = await admin_client.get("/api/v1/admin/department-access/dept-leaf")
        item = {u["name"]: u for u in get_resp.json()["units"]}["dept_mcp"]
        assert item["direct_rule"] is False
        assert item["effective_allowed"] is False

    async def test_unit_rule_put_uses_locked_unit_read(self, db_session, monkeypatch):
        await _seed_departments(db_session)
        db_session.add(_unit("dept_mcp", "department", kind="mcp"))
        await db_session.commit()
        mgr = DepartmentAccessManager(db_session)
        original = mgr._repo.get_unit_for_update
        called = False

        async def tracked(unit_name: str):
            nonlocal called
            called = True
            return await original(unit_name)

        monkeypatch.setattr(mgr._repo, "get_unit_for_update", tracked)

        await mgr.put_unit_rule("dept-leaf", "dept_mcp")

        assert called is True

    async def test_skill_rule_put_uses_locked_skill_read(self, db_session, monkeypatch):
        await _seed_departments(db_session)
        db_session.add(_skill("dept-skill", "department"))
        await db_session.commit()
        mgr = DepartmentAccessManager(db_session)
        original = mgr._repo.get_skill_for_update
        called = False

        async def tracked(slug: str):
            nonlocal called
            called = True
            return await original(slug)

        monkeypatch.setattr(mgr._repo, "get_skill_for_update", tracked)

        await mgr.put_skill_rule("dept-leaf", "dept-skill")

        assert called is True

    async def test_private_skill_is_outside_department_catalog(
        self, admin_client: AsyncClient, db_session
    ):
        await _seed_departments(db_session)
        db_session.add(_skill("private-skill", "private"))
        await db_session.commit()

        resp = await admin_client.put(
            "/api/v1/admin/department-access/dept-leaf/skills/private-skill"
        )
        assert resp.status_code == 404

        rows = (
            await db_session.execute(
                select(DepartmentSkillRule).where(
                    DepartmentSkillRule.skill_id == "private-skill"
                )
            )
        ).scalars().all()
        assert rows == []

    async def test_missing_resource_404(self, admin_client: AsyncClient, db_session):
        await _seed_departments(db_session)
        resp = await admin_client.put(
            "/api/v1/admin/department-access/dept-leaf/skills/nope"
        )
        assert resp.status_code == 404

        resp = await admin_client.put(
            "/api/v1/admin/department-access/dept-leaf/units/nope"
        )
        assert resp.status_code == 404

    async def test_concurrent_duplicate_skill_put_integrity_is_success(
        self, db_session, monkeypatch
    ):
        await _seed_departments(db_session)
        db_session.add(_skill("public-skill", "public"))
        await db_session.commit()
        mgr = DepartmentAccessManager(db_session)
        real_commit = db_session.commit

        async def fake_duplicate_commit():
            # Simulate the other transaction winning the same natural-key insert
            # after this request's SELECT but before its flush/commit.
            await db_session.rollback()
            db_session.add(
                DepartmentSkillRule(
                    department_id="dept-leaf", skill_id="public-skill"
                )
            )
            await real_commit()
            raise IntegrityError("duplicate", None, Exception())

        monkeypatch.setattr(db_session, "commit", fake_duplicate_commit)

        await mgr.put_skill_rule("dept-leaf", "public-skill")

        rows = (
            await db_session.execute(
                select(DepartmentSkillRule).where(
                    DepartmentSkillRule.department_id == "dept-leaf",
                    DepartmentSkillRule.skill_id == "public-skill",
                )
            )
        ).scalars().all()
        assert len(rows) == 1

    async def test_non_duplicate_integrity_error_is_not_swallowed(
        self, db_session, monkeypatch
    ):
        await _seed_departments(db_session)
        db_session.add(_unit("dept_mcp", "department", kind="mcp"))
        await db_session.commit()
        mgr = DepartmentAccessManager(db_session)

        async def fake_unrelated_integrity_error():
            await db_session.rollback()
            raise IntegrityError("not duplicate", None, Exception())

        monkeypatch.setattr(db_session, "commit", fake_unrelated_integrity_error)

        with pytest.raises(IntegrityError):
            await mgr.put_unit_rule("dept-leaf", "dept_mcp")
