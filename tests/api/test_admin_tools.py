"""admin external 工具 CRUD 端点集成测试(B-4)。

覆盖:auth 闸、dynamic unit 增删改、撞名 by-construction 闸、seeded 只读、
agent 挂载/卸载、凭证写-only(GET 永不回明文)。
"""

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from config import config
from db.models import Agent, AgentUnit, ToolMember, ToolUnit

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# 种子 helper(直接写 DB:agents 是 seed-only,无 create API)
# --------------------------------------------------------------------------


async def _seed_agent(db_session, name="lead_agent"):
    db_session.add(Agent(name=name, description="a", model="qwen", role_prompt="r"))
    await db_session.commit()


async def _seed_seeded_unit(db_session, name="legacy"):
    db_session.add(ToolUnit(name=name, kind="tool", description="seeded one",
                            provider="http", source="seeded", seed_hash="h"))
    db_session.add(ToolMember(unit_name=name, member_name=name, full_name=name,
                              permission="auto", definition={"endpoint": "https://x/y"}))
    await db_session.commit()


def _singleton_body(name="weather", **kw):
    body = {
        "name": name,
        "kind": "tool",
        "description": "Get weather",
        "members": [{
            "member_name": name,
            "permission": "auto",
            "endpoint": "https://api.example.com/weather",
            "method": "GET",
            "parameters": [{"name": "city", "type": "string", "required": True}],
        }],
    }
    body.update(kw)
    return body


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


class TestAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        assert (await anon_client.get("/api/v1/admin/tools/units")).status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        assert (await client.get("/api/v1/admin/tools/units")).status_code == 403


# --------------------------------------------------------------------------
# unit CRUD
# --------------------------------------------------------------------------


class TestUnitCrud:
    async def test_create_singleton(self, admin_client: AsyncClient):
        resp = await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "weather"
        assert body["source"] == "dynamic"
        assert body["provider"] == "http"
        assert len(body["members"]) == 1
        assert body["members"][0]["full_name"] == "weather"   # singleton full_name == unit

    async def test_create_toolset_prefixes_full_names(self, admin_client: AsyncClient):
        body = {
            "name": "github",
            "kind": "toolset",
            "description": "GitHub",
            "members": [
                {"member_name": "search_repos", "endpoint": "https://api.github.com/search"},
                {"member_name": "create_issue", "endpoint": "https://api.github.com/issues",
                 "method": "POST"},
            ],
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 201, resp.text
        fns = {m["full_name"] for m in resp.json()["members"]}
        assert fns == {"github__search_repos", "github__create_issue"}

    async def test_create_collides_with_builtin_name(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/tools/units", json=_singleton_body(name="web_search")
        )
        assert resp.status_code == 409
        assert "builtin" in resp.json()["detail"]

    async def test_create_unit_name_with_double_underscore(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/tools/units", json=_singleton_body(name="bad__name")
        )
        assert resp.status_code == 400
        assert "__" in resp.json()["detail"]

    async def test_create_duplicate(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        assert resp.status_code == 409

    async def test_full_name_collision_across_units(self, admin_client: AsyncClient):
        # toolset github 占了 github__x;另一 unit 想用同 full_name → 409
        await admin_client.post("/api/v1/admin/tools/units", json={
            "name": "github", "kind": "toolset", "description": "g",
            "members": [{"member_name": "x", "endpoint": "https://a/b"}],
        })
        # 直接做不出跨 unit 同 full_name(prefix=unit 名),故构造 singleton 名 == 已存 full_name
        resp = await admin_client.post(
            "/api/v1/admin/tools/units", json=_singleton_body(name="github__x")
        )
        # singleton 名禁 `__` 先被拦(也是撞名的一种 by-construction 防线)
        assert resp.status_code == 400

    async def test_get_missing_404(self, admin_client: AsyncClient):
        assert (await admin_client.get("/api/v1/admin/tools/units/nope")).status_code == 404

    async def test_update_dynamic(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather",
            json=_singleton_body(description="changed"),
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "changed"

    async def test_delete_dynamic(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        assert (await admin_client.delete("/api/v1/admin/tools/units/weather")).status_code == 204
        assert (await admin_client.get("/api/v1/admin/tools/units/weather")).status_code == 404


class TestSeededReadOnly:
    async def test_update_seeded_409(self, admin_client: AsyncClient, db_session):
        await _seed_seeded_unit(db_session)
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/legacy", json=_singleton_body(name="legacy")
        )
        assert resp.status_code == 409
        assert "seeded" in resp.json()["detail"]

    async def test_delete_seeded_409(self, admin_client: AsyncClient, db_session):
        await _seed_seeded_unit(db_session)
        assert (await admin_client.delete("/api/v1/admin/tools/units/legacy")).status_code == 409


# --------------------------------------------------------------------------
# agent 挂载
# --------------------------------------------------------------------------


class TestMount:
    async def test_mount_and_unmount(self, admin_client: AsyncClient, db_session):
        await _seed_agent(db_session)
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/agents/lead_agent",
            json={"member_state": "enabled"},
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "dynamic"
        # GET 反映挂载
        unit = (await admin_client.get("/api/v1/admin/tools/units/weather")).json()
        assert any(a["agent_name"] == "lead_agent" for a in unit["mounted_agents"])
        # 卸载
        assert (await admin_client.delete(
            "/api/v1/admin/tools/units/weather/agents/lead_agent"
        )).status_code == 204

    async def test_mount_unknown_agent_400(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/agents/ghost",
            json={"member_state": "enabled"},
        )
        assert resp.status_code == 400

    async def test_cannot_override_seeded_binding(self, admin_client: AsyncClient, db_session):
        await _seed_agent(db_session)
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        # 预置一条 seeded agent_unit(模拟 agent MD 声明)
        db_session.add(AgentUnit(agent_name="lead_agent", unit_name="weather",
                                 member_state="enabled", source="seeded"))
        await db_session.commit()
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/agents/lead_agent",
            json={"member_state": "disabled"},
        )
        assert resp.status_code == 409

    async def test_list_agents(self, admin_client: AsyncClient, db_session):
        await _seed_agent(db_session, "research_agent")
        agents = (await admin_client.get("/api/v1/admin/tools/agents")).json()["agents"]
        assert any(a["name"] == "research_agent" for a in agents)


# --------------------------------------------------------------------------
# 凭证(写-only)
# --------------------------------------------------------------------------


class TestCredentials:
    @pytest.fixture
    def key(self, monkeypatch):
        monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())

    async def test_set_credential_masked_in_get(self, admin_client: AsyncClient, key):
        # endpoint 引用 {{TOOL_SECRET_K}} 占位符
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)

        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K",
            json={"value": "live-secret-value"},
        )
        assert resp.status_code == 204

        unit = (await admin_client.get("/api/v1/admin/tools/units/weather")).json()
        cred = next(c for c in unit["credentials"] if c["placeholder"] == "TOOL_SECRET_K")
        assert cred["configured"] is True
        assert cred["source"] == "dynamic"
        # 明文 / 密文 绝不出现在响应任何角落
        assert "live-secret-value" not in resp.text
        assert "live-secret-value" not in (await admin_client.get(
            "/api/v1/admin/tools/units/weather")).text

    async def test_referenced_but_unconfigured_shows_false(self, admin_client: AsyncClient, key):
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        unit = (await admin_client.get("/api/v1/admin/tools/units/weather")).json()
        cred = next(c for c in unit["credentials"] if c["placeholder"] == "TOOL_SECRET_K")
        assert cred["configured"] is False

    async def test_set_credential_on_seeded_409(self, admin_client: AsyncClient, db_session, key):
        await _seed_seeded_unit(db_session)
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/legacy/credentials/TOOL_SECRET_K",
            json={"value": "x"},
        )
        assert resp.status_code == 409
