"""Admin department access API tests (G-1).

Coverage: auth, effective display, direct/inherited rules, idempotent rule
mutation, private skill rejection, and missing department/resource mapping.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

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
        slug=slug,
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
                department_id="dept-root", skill_slug="public-skill"
            ),
            DepartmentSkillRule(
                department_id="dept-leaf", skill_slug="dept-skill"
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

    async def test_private_skill_rule_rejected_without_writing_rule(
        self, admin_client: AsyncClient, db_session
    ):
        await _seed_departments(db_session)
        db_session.add(_skill("private-skill", "private"))
        await db_session.commit()

        resp = await admin_client.put(
            "/api/v1/admin/department-access/dept-leaf/skills/private-skill"
        )
        assert resp.status_code == 400
        assert "private skill" in resp.json()["detail"]

        rows = (
            await db_session.execute(
                select(DepartmentSkillRule).where(
                    DepartmentSkillRule.skill_slug == "private-skill"
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
