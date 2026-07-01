"""用户侧 skill 管理 REST(C-3):GET 列可见 skill、PUT 个人 toggle。"""

import pytest
from httpx import AsyncClient

from db.models import Skill


async def _seed_skill(db_session, slug, visibility="public", default_enabled=True):
    db_session.add(Skill(
        slug=slug, name=slug.title(), description="d", visibility=visibility,
        default_enabled=default_enabled, source="seeded", skill_md="body",
    ))
    await db_session.commit()


class TestListSkills:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        assert (await anon_client.get("/api/v1/skills")).status_code == 401

    async def test_lists_visible_with_effective_state(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub", default_enabled=True)
        await _seed_skill(db_session, "off", default_enabled=False)
        await _seed_skill(db_session, "priv", visibility="private")  # 非 owner → 不可见

        r = await client.get("/api/v1/skills")
        assert r.status_code == 200
        items = {s["slug"]: s for s in r.json()["skills"]}
        assert set(items) == {"pub", "off"}          # private 不列
        assert items["pub"]["enabled"] is True
        assert items["off"]["enabled"] is False
        assert items["pub"]["is_overridden"] is False


class TestToggleSkill:
    async def test_toggle_persists(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "pub", default_enabled=True)

        r = await client.put("/api/v1/skills/pub/enabled", json={"enabled": False})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False and body["is_overridden"] is True

        # 重列反映覆盖态
        items = {s["slug"]: s for s in (await client.get("/api/v1/skills")).json()["skills"]}
        assert items["pub"]["enabled"] is False
        assert items["pub"]["default_enabled"] is True

    async def test_toggle_invisible_404(self, client: AsyncClient, db_session):
        await _seed_skill(db_session, "priv", visibility="private")  # owner 非当前用户
        r = await client.put("/api/v1/skills/priv/enabled", json={"enabled": True})
        assert r.status_code == 404

    async def test_toggle_unknown_404(self, client: AsyncClient):
        r = await client.put("/api/v1/skills/ghost/enabled", json={"enabled": True})
        assert r.status_code == 404

    async def test_anon_blocked(self, anon_client: AsyncClient):
        r = await anon_client.put("/api/v1/skills/pub/enabled", json={"enabled": True})
        assert r.status_code == 401
