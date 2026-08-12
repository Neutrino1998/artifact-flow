"""admin external 工具 CRUD 端点集成测试(B-4)。

覆盖:auth 闸、dynamic unit 增删改、撞名 by-construction 闸、seeded 只读、
agent 挂载/卸载、凭证写-only(GET 永不回明文)。
"""

import io
import zipfile

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from api.dependencies import get_mcp_client_manager
from config import config
from core.management.tool_registry_manager import ToolRegistryManager
from db.models import Agent, AgentUnit, ToolMember, ToolUnit
from tools.custom.seed_bundle import MAX_SEED_UPLOAD_BYTES
from tools.custom.mcp_client import McpListResult, McpToolDefinition

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# 种子 helper(直接写 DB:agents 是 seed-only,无 create API)
# --------------------------------------------------------------------------


async def _seed_agent(db_session, name="lead_agent", internal=False):
    db_session.add(Agent(name=name, description="a", model="qwen", role_prompt="r",
                         internal=internal))
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
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        }],
    }
    body.update(kw)
    return body


def _mcp_body(name="reports_mcp", **kw):
    body = {
        "name": name,
        "kind": "mcp",
        "description": "Reports MCP",
        "defer": True,
        "members": [],
        "provider_config": {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {},
            "timeout": 60,
            "default_permission": "confirm",
        },
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

    async def test_update_dynamic_uses_locked_unit_read(self, db_session, monkeypatch):
        db_session.add(ToolUnit(
            name="weather", kind="tool", description="Get weather", source="dynamic"
        ))
        db_session.add(ToolMember(
            unit_name="weather",
            member_name="weather",
            full_name="weather",
            permission="auto",
            definition={"endpoint": "https://api.example.com/weather"},
        ))
        await db_session.commit()
        mgr = ToolRegistryManager(db_session)
        original = mgr._registry.get_unit_for_update
        called = False

        async def tracked(name: str):
            nonlocal called
            called = True
            return await original(name)

        monkeypatch.setattr(mgr._registry, "get_unit_for_update", tracked)

        await mgr.update_unit("weather", _singleton_body(description="changed"))

        assert called is True

    async def test_update_dynamic_returns_fresh_member_definition(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["description"] = "old member description"
        await admin_client.post("/api/v1/admin/tools/units", json=body)

        body["members"][0]["description"] = "new member description"
        resp = await admin_client.put("/api/v1/admin/tools/units/weather", json=body)

        assert resp.status_code == 200, resp.text
        assert resp.json()["members"][0]["definition"]["description"] == "new member description"

    async def test_delete_dynamic(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        assert (await admin_client.delete("/api/v1/admin/tools/units/weather")).status_code == 204
        assert (await admin_client.get("/api/v1/admin/tools/units/weather")).status_code == 404

    async def test_update_cannot_change_kind(self, admin_client: AsyncClient):
        # kind 不可变(决定 full_name 形状)→ 改 = 409
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())  # tool
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather", json=_singleton_body(kind="toolset")
        )
        assert resp.status_code == 409
        assert "kind" in resp.json()["detail"]

    async def test_create_rejects_non_whitelist_secret_ref(self, admin_client: AsyncClient):
        # {{JWT_SECRET}} 非 TOOL_SECRET_ 前缀 → 400(与 seeds/loader 同口径,reviewer #15)
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{JWT_SECRET}}"}
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "TOOL_SECRET_" in resp.json()["detail"]

    async def test_create_rejects_placeholder_in_param_default(self, admin_client: AsyncClient):
        # 参数 default 不是 secret 注入点 → 含 {{...}} 即 400(sweep minor)
        body = _singleton_body()
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "q": {"type": "string", "default": "{{TOOL_SECRET_K}}"},
            },
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "default" in resp.json()["detail"]

    async def test_create_rejects_bad_response_extract(self, admin_client: AsyncClient):
        # response_extract 语法错 → 400(JMESPath 写入边界校验,与 seeds reconcile 同口径)
        body = _singleton_body()
        body["members"][0]["response_extract"] = "data["
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "JMESPath" in resp.json()["detail"]

    async def test_create_accepts_valid_response_extract(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["response_extract"] = "data.items[*].id"
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 201

    async def test_create_accepts_json_parameter_default(self, admin_client: AsyncClient):
        body = _singleton_body(name="ragflow_retrieval")
        body["members"][0]["method"] = "POST"
        default_ids = ["c750d2f6752411f191e693d1a844b0ba"]
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "dataset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": default_ids,
                    "enum": [default_ids],
                },
            },
            "required": ["question", "dataset_ids"],
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 201, resp.text
        schema = resp.json()["members"][0]["definition"]["input_schema"]
        dataset_ids = schema["properties"]["dataset_ids"]
        assert dataset_ids["type"] == "array"
        assert dataset_ids["default"] == default_ids
        assert dataset_ids["enum"] == [default_ids]

    async def test_create_rejects_json_parameter_scalar_default(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "payload": {"type": "object", "default": "not-an-object"},
            },
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "payload" in resp.json()["detail"]
        assert "default" in resp.json()["detail"]
        assert "does not satisfy" in resp.json()["detail"]

    async def test_create_rejects_invalid_json_schema_type(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {"payload": {"type": "not-a-json-schema-type"}},
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "invalid JSON Schema" in resp.json()["detail"]

    async def test_create_accepts_text_artifact_output(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["artifact_output"] = {
            "enabled": True,
            "mode": "text",
            "content_type": "text/csv",
            "filename": "weather.csv",
            "title": "Weather CSV",
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 201, resp.text
        definition = resp.json()["members"][0]["definition"]
        assert definition["artifact_output"] == {
            "enabled": True,
            "mode": "text",
            "content_type": "text/csv",
            "filename": "weather.csv",
            "title": "Weather CSV",
        }

    async def test_create_accepts_binary_artifact_output_without_content_type(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["artifact_output"] = {"enabled": True, "mode": "binary"}
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 201, resp.text
        assert resp.json()["members"][0]["definition"]["artifact_output"] == {
            "enabled": True,
            "mode": "binary",
            "content_type": None,
            "filename": None,
            "title": None,
        }

    async def test_create_accepts_url_path_parameters(self, admin_client: AsyncClient):
        body = _singleton_body(name="ragflow_download")
        body["members"][0]["endpoint"] = (
            "https://ragflow.example.com/api/v1/datasets/{dataset_id}/documents/{document_id}"
        )
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "dataset_id": {"type": "string"},
                "document_id": {"type": "string"},
            },
            "required": ["dataset_id", "document_id"],
        }
        body["members"][0]["artifact_output"] = {"enabled": True, "mode": "binary"}

        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)

        assert resp.status_code == 201, resp.text
        definition = resp.json()["members"][0]["definition"]
        assert definition["endpoint"].endswith(
            "/datasets/{dataset_id}/documents/{document_id}"
        )

    async def test_create_rejects_undeclared_url_path_parameter(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["endpoint"] = (
            "https://api.example.com/datasets/{dataset_id}/documents/{document_id}"
        )
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {"dataset_id": {"type": "string"}},
            "required": ["dataset_id"],
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "document_id" in resp.json()["detail"]
        assert "declared schema property" in resp.json()["detail"]

    async def test_create_rejects_url_path_parameter_in_host(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["endpoint"] = "https://{host}/api/v1/documents/{document_id}"
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "document_id": {"type": "string"},
            },
            "required": ["host", "document_id"],
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "only allowed in the path" in resp.json()["detail"]

    async def test_create_rejects_binary_artifact_output_with_response_extract(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["response_extract"] = "data.file"
        body["members"][0]["artifact_output"] = {
            "enabled": True,
            "mode": "binary",
            "content_type": "application/pdf",
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "response_extract" in resp.json()["detail"]

    async def test_create_rejects_overlong_artifact_output_content_type(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["artifact_output"] = {
            "enabled": True,
            "mode": "text",
            "content_type": "x" * 129,
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 422

    async def test_create_rejects_overlong_artifact_output_title(self, admin_client: AsyncClient):
        body = _singleton_body()
        body["members"][0]["artifact_output"] = {
            "enabled": True,
            "mode": "text",
            "title": "x" * 257,
        }
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 422

    async def test_create_mcp_unit(self, admin_client: AsyncClient):
        resp = await admin_client.post("/api/v1/admin/tools/units", json=_mcp_body())
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "mcp"
        assert body["provider"] == "mcp"
        assert body["members"] == []
        assert body["defer"] is True
        assert body["provider_config"] == {
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "headers": {},
            "timeout": 60,
            "default_permission": "confirm",
        }

    async def test_create_mcp_rejects_missing_url(self, admin_client: AsyncClient):
        body = _mcp_body()
        body["provider_config"]["url"] = ""
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "URL" in resp.json()["detail"]

    async def test_create_mcp_rejects_http_members(self, admin_client: AsyncClient):
        body = _mcp_body(members=[{"member_name": "x", "endpoint": "https://x/y"}])
        resp = await admin_client.post("/api/v1/admin/tools/units", json=body)
        assert resp.status_code == 400
        assert "members" in resp.json()["detail"]

    async def test_update_mcp_provider_config(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_mcp_body())
        body = _mcp_body(description="changed")
        body["provider_config"]["timeout"] = 15
        body["provider_config"]["default_permission"] = "auto"
        resp = await admin_client.put("/api/v1/admin/tools/units/reports_mcp", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["description"] == "changed"
        assert resp.json()["provider_config"]["timeout"] == 15
        assert resp.json()["provider_config"]["default_permission"] == "auto"

    async def test_update_cannot_change_mcp_kind(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_mcp_body())
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/reports_mcp",
            json=_singleton_body(name="reports_mcp"),
        )
        assert resp.status_code == 409
        assert "kind" in resp.json()["detail"]


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


class TestSeedImportExport:
    @pytest.fixture
    def key(self, monkeypatch):
        monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())

    @staticmethod
    def _zip_with(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    @staticmethod
    def _corrupt_member_zip():
        data = bytearray(TestSeedImportExport._zip_with({
            "tools/weather.md": b"""---
name: weather
type: http
endpoint: https://api.example.com/weather
---
""",
        }))
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo("tools/weather.md")
            name_len = len(info.filename.encode("utf-8"))
            data_offset = info.header_offset + 30 + name_len + len(info.extra)
            data[data_offset] ^= 0xFF
        return bytes(data)

    @staticmethod
    def _unsupported_compression_zip():
        data = bytearray(TestSeedImportExport._zip_with({
            "tools/weather.md": b"""---
name: weather
type: http
endpoint: https://api.example.com/weather
---
""",
        }))
        for signature, method_offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
            pos = data.find(signature)
            assert pos >= 0
            data[pos + method_offset:pos + method_offset + 2] = (99).to_bytes(2, "little")
        return bytes(data)

    async def test_export_dynamic_seed_bundle_masks_credentials(
        self, admin_client: AsyncClient, key
    ):
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        await admin_client.put(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K",
            json={"value": "live-secret-value"},
        )

        resp = await admin_client.get("/api/v1/admin/tools/units/weather/export")

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        assert "weather-tool-seed.zip" in resp.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
            payload = "\n".join(zf.read(name).decode("utf-8") for name in names)
        assert names == ["tools/weather.md"]
        assert "{{TOOL_SECRET_K}}" in payload
        assert "live-secret-value" not in payload

    async def test_import_exported_tool_seed_as_dynamic(
        self, admin_client: AsyncClient
    ):
        body = _singleton_body(name="weather_seed")
        body["members"][0]["input_schema"] = {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "payload": {"type": "object", "default": {"k": "v"}},
            },
            "required": ["city"],
        }
        body["members"][0]["artifact_output"] = {
            "enabled": True,
            "mode": "text",
            "content_type": "text/plain",
            "filename": "weather.txt",
        }
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        exported = await admin_client.get("/api/v1/admin/tools/units/weather_seed/export")
        await admin_client.delete("/api/v1/admin/tools/units/weather_seed")

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("weather_seed.zip", exported.content, "application/zip")},
        )

        assert resp.status_code == 201, resp.text
        unit = resp.json()["unit"]
        assert unit["name"] == "weather_seed"
        assert unit["source"] == "dynamic"
        definition = unit["members"][0]["definition"]
        assert definition["input_schema"]["properties"]["payload"]["default"] == {"k": "v"}
        assert definition["artifact_output"]["filename"] == "weather.txt"

    async def test_import_single_mcp_markdown_seed(self, admin_client: AsyncClient):
        md = b"""---
name: reports_mcp
description: Reports MCP
type: mcp
transport: streamable_http
url: https://mcp.example.com/mcp
headers:
  Authorization: Bearer {{TOOL_SECRET_MCP_TOKEN}}
default_permission: auto
---
"""

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("reports_mcp.md", md, "text/markdown")},
        )

        assert resp.status_code == 201, resp.text
        unit = resp.json()["unit"]
        assert unit["kind"] == "mcp"
        assert unit["source"] == "dynamic"
        assert unit["provider_config"]["default_permission"] == "auto"
        assert {c["placeholder"] for c in unit["credentials"]} == {
            "TOOL_SECRET_MCP_TOKEN"
        }

    async def test_import_single_http_markdown_seed_defaults_type(self, admin_client: AsyncClient):
        md = b"""---
name: weather_md
endpoint: https://api.example.com/weather
---
"""

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("weather_md.md", md, "text/markdown")},
        )

        assert resp.status_code == 201, resp.text
        unit = resp.json()["unit"]
        assert unit["name"] == "weather_md"
        assert unit["kind"] == "tool"
        assert unit["members"][0]["definition"]["endpoint"] == "https://api.example.com/weather"

    async def test_import_single_mcp_like_markdown_seed_requires_type(self, admin_client: AsyncClient):
        md = b"""---
name: reports_mcp
url: https://mcp.example.com/mcp
transport: streamable_http
default_permission: confirm
---
"""

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("reports_mcp.md", md, "text/markdown")},
        )

        assert resp.status_code == 400
        assert "type" in resp.json()["detail"]

    async def test_import_corrupt_zip_member_returns_400(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("broken.zip", self._corrupt_member_zip(), "application/zip")},
        )

        assert resp.status_code == 400
        assert "extract" in resp.json()["detail"]

    async def test_import_unsupported_zip_compression_returns_400(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={
                "file": (
                    "unsupported-compression.zip",
                    self._unsupported_compression_zip(),
                    "application/zip",
                )
            },
        )

        assert resp.status_code == 400
        assert "extract" in resp.json()["detail"]

    async def test_import_non_utf8_markdown_in_zip_returns_400(self, admin_client: AsyncClient):
        blob = self._zip_with({"tools/weather.md": b"\xff\xfe\x00"})

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("bad-encoding.zip", blob, "application/zip")},
        )

        assert resp.status_code == 400
        assert "UTF-8" in resp.json()["detail"]

    async def test_import_rejects_oversize_before_parse(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={
                "file": (
                    "huge.md",
                    b"x" * (MAX_SEED_UPLOAD_BYTES + 1),
                    "text/markdown",
                )
            },
        )

        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"]

    async def test_import_rejects_multiple_units(self, admin_client: AsyncClient):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "tools/a.md",
                """---
name: a
type: http
endpoint: https://api.example.com/a
---
""",
            )
            zf.writestr(
                "tools/b.md",
                """---
name: b
type: http
endpoint: https://api.example.com/b
---
""",
            )

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("tools.zip", buf.getvalue(), "application/zip")},
        )

        assert resp.status_code == 400
        assert "exactly one" in resp.json()["detail"]

    async def test_import_collision_409(self, admin_client: AsyncClient):
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        md = b"""---
name: weather
type: http
endpoint: https://api.example.com/weather
---
"""

        resp = await admin_client.post(
            "/api/v1/admin/tools/units/import",
            files={"file": ("weather.md", md, "text/markdown")},
        )

        assert resp.status_code == 409


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

    async def test_mount_internal_agent_rejected(self, admin_client: AsyncClient, db_session):
        # 内部 agent(compact_agent 等)不跑工具循环 → 挂载端点拒绝
        await _seed_agent(db_session, "compact_agent", internal=True)
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/agents/compact_agent",
            json={"member_state": "enabled"},
        )
        assert resp.status_code == 400
        assert "internal" in resp.json()["detail"]

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

    async def test_list_agents_excludes_internal(self, admin_client: AsyncClient, db_session):
        # 挂载列表只含可挂载目标 —— 内部 agent 不出现
        await _seed_agent(db_session, "research_agent")
        await _seed_agent(db_session, "compact_agent", internal=True)
        agents = (await admin_client.get("/api/v1/admin/tools/agents")).json()["agents"]
        names = [a["name"] for a in agents]
        assert "research_agent" in names
        assert "compact_agent" not in names


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

    async def test_mcp_provider_config_references_credentials(self, admin_client: AsyncClient, key):
        body = _mcp_body()
        body["provider_config"]["url"] = "https://{{TOOL_SECRET_MCP_HOST}}/mcp"
        body["provider_config"]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_MCP_TOKEN}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        unit = (await admin_client.get("/api/v1/admin/tools/units/reports_mcp")).json()
        assert {c["placeholder"] for c in unit["credentials"]} == {
            "TOOL_SECRET_MCP_HOST",
            "TOOL_SECRET_MCP_TOKEN",
        }

    async def test_set_credential_on_seeded_409(self, admin_client: AsyncClient, db_session, key):
        await _seed_seeded_unit(db_session)
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/legacy/credentials/TOOL_SECRET_K",
            json={"value": "x"},
        )
        assert resp.status_code == 409

    async def test_set_credential_unreferenced_placeholder_400(self, admin_client: AsyncClient, key):
        # unit 定义未引用 {{TOOL_SECRET_X}} → 配它是配不上的孤儿 → 400(reviewer #9)
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())  # 无 headers
        resp = await admin_client.put(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_X",
            json={"value": "v"},
        )
        assert resp.status_code == 400
        assert "not referenced" in resp.json()["detail"]

    async def test_delete_credential_on_seeded_409(self, admin_client: AsyncClient, db_session, key):
        # seeded 凭证归 reconciler,UI 不能删(对称 set,reviewer #2)
        await _seed_seeded_unit(db_session)
        resp = await admin_client.delete(
            "/api/v1/admin/tools/units/legacy/credentials/TOOL_SECRET_K"
        )
        assert resp.status_code == 409

    async def test_delete_nonexistent_credential_404(self, admin_client: AsyncClient, key):
        # 引用了占位符但从未配过 → 删 = no-op → 404(不给假"已删",对称 unmount)
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        resp = await admin_client.delete(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K"
        )
        assert resp.status_code == 404

    async def test_delete_existing_credential_204(self, admin_client: AsyncClient, key):
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        await admin_client.put(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K", json={"value": "v"}
        )
        resp = await admin_client.delete(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K"
        )
        assert resp.status_code == 204

    async def test_placeholder_too_long_422(self, admin_client: AsyncClient, key):
        # 路径参数 >128 → 边界 422(不漏到 asyncpg 截断 500,reviewer #10)
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.put(
            f"/api/v1/admin/tools/units/weather/credentials/{'X' * 200}", json={"value": "v"}
        )
        assert resp.status_code == 422

    async def test_update_prunes_dereferenced_dynamic_credential(self, admin_client: AsyncClient, key):
        # 配了引用的凭证 → 改定义去掉该引用 → update 后凭证被 prune(对称 reconciler,reviewer #9)
        body = _singleton_body()
        body["members"][0]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_K}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        await admin_client.put(
            "/api/v1/admin/tools/units/weather/credentials/TOOL_SECRET_K", json={"value": "v"}
        )
        # 改定义去掉 header(不再引用 TOOL_SECRET_K)
        await admin_client.put("/api/v1/admin/tools/units/weather", json=_singleton_body())
        unit = (await admin_client.get("/api/v1/admin/tools/units/weather")).json()
        assert all(c["placeholder"] != "TOOL_SECRET_K" for c in unit["credentials"])

    async def test_update_prunes_dereferenced_mcp_credential(self, admin_client: AsyncClient, key):
        body = _mcp_body()
        body["provider_config"]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_MCP_TOKEN}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        await admin_client.put(
            "/api/v1/admin/tools/units/reports_mcp/credentials/TOOL_SECRET_MCP_TOKEN",
            json={"value": "v"},
        )
        await admin_client.put("/api/v1/admin/tools/units/reports_mcp", json=_mcp_body())
        unit = (await admin_client.get("/api/v1/admin/tools/units/reports_mcp")).json()
        assert all(c["placeholder"] != "TOOL_SECRET_MCP_TOKEN" for c in unit["credentials"])


# --------------------------------------------------------------------------
# MCP 保存态连通性测试
# --------------------------------------------------------------------------


class FakeMcpManager:
    def __init__(self, *, error=None):
        self.error = error
        self.invalidated = []
        self.calls = []
        self.resolved = None

    async def invalidate(self, unit_name):
        self.invalidated.append(unit_name)

    async def list_tools(self, unit_name, provider_config, *, credential_resolver=None):
        if credential_resolver is not None:
            self.resolved = await credential_resolver.resolve(unit_name)
        self.calls.append((unit_name, provider_config))
        if self.error:
            return McpListResult(tools=[], error=self.error)
        return McpListResult(
            tools=[
                McpToolDefinition(name="report", description="r", input_schema={}),
                McpToolDefinition(name="chart", description="c", input_schema={}),
            ]
        )


class TestMcpConnectionTest:
    async def test_test_mcp_unit_uses_saved_config_and_credentials(
        self, app, admin_client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
        fake = FakeMcpManager()
        app.dependency_overrides[get_mcp_client_manager] = lambda: fake

        body = _mcp_body()
        body["provider_config"]["headers"] = {"Authorization": "Bearer {{TOOL_SECRET_MCP_TOKEN}}"}
        await admin_client.post("/api/v1/admin/tools/units", json=body)
        await admin_client.put(
            "/api/v1/admin/tools/units/reports_mcp/credentials/TOOL_SECRET_MCP_TOKEN",
            json={"value": "live-token"},
        )

        resp = await admin_client.post("/api/v1/admin/tools/units/reports_mcp/test")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "success": True,
            "message": "discovered 2 MCP tools",
            "tool_count": 2,
        }
        assert fake.invalidated == ["reports_mcp"]
        assert fake.calls[0][0] == "reports_mcp"
        assert fake.calls[0][1]["url"] == "https://mcp.example.com/mcp"
        assert fake.resolved == {"TOOL_SECRET_MCP_TOKEN": "live-token"}

    async def test_test_mcp_unit_returns_failure_payload(self, app, admin_client: AsyncClient):
        fake = FakeMcpManager(error="MCP server is unavailable")
        app.dependency_overrides[get_mcp_client_manager] = lambda: fake
        await admin_client.post("/api/v1/admin/tools/units", json=_mcp_body())

        resp = await admin_client.post("/api/v1/admin/tools/units/reports_mcp/test")
        assert resp.status_code == 200
        assert resp.json() == {
            "success": False,
            "message": "MCP server is unavailable",
            "tool_count": 0,
        }

    async def test_test_non_mcp_unit_rejected(self, app, admin_client: AsyncClient):
        app.dependency_overrides[get_mcp_client_manager] = lambda: FakeMcpManager()
        await admin_client.post("/api/v1/admin/tools/units", json=_singleton_body())
        resp = await admin_client.post("/api/v1/admin/tools/units/weather/test")
        assert resp.status_code == 400
        assert "MCP" in resp.json()["detail"]
