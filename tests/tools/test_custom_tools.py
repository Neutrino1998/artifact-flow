"""Custom HTTP tools using native JSON Schema arguments."""

import json

import pytest

from tools.artifact_output import filename_from_headers, normalize_artifact_output_config
from tools.base import ToolPermission
from tools.custom.http_tool import HttpTool, HttpToolConfig, validate_response_extract
from tools.custom.loader import load_custom_tool, load_custom_tools
from tools.custom.secrets import SecretResolutionError, resolve_secrets
from tools.custom.url_template import validate_url_path_template


EMPTY_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class TestResolveSecrets:
    def test_resolves_nested_values(self, monkeypatch):
        monkeypatch.setenv("TOOL_SECRET_TOKEN", "abc")
        value = {
            "Authorization": "Bearer {{TOOL_SECRET_TOKEN}}",
            "nested": ["{{TOOL_SECRET_TOKEN}}", "plain"],
        }
        assert resolve_secrets(value) == {
            "Authorization": "Bearer abc",
            "nested": ["abc", "plain"],
        }

    def test_rejects_non_prefixed_variable(self, monkeypatch):
        monkeypatch.setenv("ARTIFACTFLOW_JWT_SECRET", "secret")
        with pytest.raises(SecretResolutionError, match="prefix"):
            resolve_secrets("{{ARTIFACTFLOW_JWT_SECRET}}")

    def test_rejects_missing_secret(self, monkeypatch):
        monkeypatch.delenv("TOOL_SECRET_MISSING", raising=False)
        with pytest.raises(SecretResolutionError, match="not set"):
            resolve_secrets("{{TOOL_SECRET_MISSING}}")


class TestDefinitionLoading:
    def test_loads_and_exports_full_json_schema_without_control_properties(self, tmp_path):
        path = tmp_path / "lookup.md"
        path.write_text(
            """---
name: inventory_lookup
description: Inventory lookup
type: http
endpoint: https://api.example.com/items
method: POST
input_schema:
  type: object
  properties:
    sku:
      type: string
      enum: [A-1, B-2]
    filters:
      type: object
      properties:
        warehouse: {type: string}
      additionalProperties: false
    limit: {type: integer, default: 10}
  required: [sku]
  additionalProperties: false
---
Detailed usage.
""",
            encoding="utf-8",
        )

        tool = load_custom_tool(str(path))

        assert isinstance(tool, HttpTool)
        assert tool.permission is ToolPermission.CONFIRM
        assert "Detailed usage" in tool.description
        business = tool.get_input_schema()
        assert business["properties"]["filters"]["type"] == "object"
        exported = tool.to_native_tool_schema()["function"]["parameters"]
        assert exported == business

    def test_legacy_parameters_are_rejected(self, tmp_path):
        path = tmp_path / "legacy.md"
        path.write_text(
            """---
name: legacy
type: http
endpoint: https://api.example.com
parameters: []
---
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="legacy 'parameters'"):
            load_custom_tool(str(path))

    def test_double_underscore_property_is_an_ordinary_business_parameter(self, tmp_path):
        path = tmp_path / "ordinary.md"
        path.write_text(
            """---
name: ordinary
type: http
endpoint: https://api.example.com
input_schema:
  type: object
  properties:
    __reason: {type: string}
---
""",
            encoding="utf-8",
        )
        tool = load_custom_tool(str(path))

        assert "__reason" in tool.get_input_schema()["properties"]

    def test_loader_skips_hidden_disabled_and_invalid_files(self, tmp_path):
        valid = """---
name: valid
type: http
endpoint: https://api.example.com
input_schema: {type: object, properties: {}}
---
"""
        (tmp_path / "valid.md").write_text(valid, encoding="utf-8")
        (tmp_path / "_disabled.md").write_text(valid, encoding="utf-8")
        (tmp_path / ".hidden.md").write_text(valid, encoding="utf-8")
        (tmp_path / "broken.md").write_text("not frontmatter", encoding="utf-8")

        assert [tool.name for tool in load_custom_tools(str(tmp_path))] == ["valid"]


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = '{"ok": 1}'
    content = text.encode()

    def __init__(self, payload=None, headers=None, content=None):
        self._payload = {"ok": 1} if payload is None else payload
        if headers is not None:
            self.headers = headers
        if content is not None:
            self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client_class(seen, response=None):
    class _Client:
        def __init__(self, *args, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            seen.update(method=method, url=url, request_kwargs=kwargs)
            return response or _FakeResponse()

    return _Client


class TestHttpNativeArguments:
    async def test_post_preserves_nested_json_and_applies_default(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "tools.custom.http_tool.httpx.AsyncClient", _client_class(seen)
        )
        tool = HttpTool(HttpToolConfig(
            name="lookup",
            description="lookup",
            permission="auto",
            endpoint="http://10.0.0.1/lookup",
            method="POST",
            input_schema={
                "type": "object",
                "properties": {
                    "filters": {"type": "object"},
                    "ids": {"type": "array", "items": {"type": "integer"}},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["filters", "ids"],
                "additionalProperties": False,
            },
        ))

        result = await tool(filters={"warehouse": "east"}, ids=[1, 2])

        assert result.success is True
        assert seen["request_kwargs"]["json"] == {
            "filters": {"warehouse": "east"},
            "ids": [1, 2],
            "limit": 5,
        }

    async def test_get_encodes_non_scalars_as_compact_stable_json(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "tools.custom.http_tool.httpx.AsyncClient", _client_class(seen)
        )
        tool = HttpTool(HttpToolConfig(
            name="search",
            description="search",
            permission="auto",
            endpoint="http://10.0.0.1/search",
            method="GET",
            input_schema={
                "type": "object",
                "properties": {
                    "filters": {"type": "object"},
                    "ids": {"type": "array"},
                    "active": {"type": "boolean"},
                },
                "required": ["filters", "ids", "active"],
            },
        ))

        result = await tool(filters={"z": 1, "a": 2}, ids=[2, 1], active=True)

        assert result.success is True
        assert seen["request_kwargs"]["params"] == {
            "filters": '{"a":2,"z":1}',
            "ids": "[2,1]",
            "active": "true",
        }

    async def test_path_scalar_is_encoded_and_removed_from_query(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "tools.custom.http_tool.httpx.AsyncClient", _client_class(seen)
        )
        tool = HttpTool(HttpToolConfig(
            name="download",
            description="download",
            permission="auto",
            endpoint="http://10.0.0.1/datasets/{dataset_id}/documents/{document_id}",
            method="GET",
            input_schema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "document_id": {"type": "string"},
                    "view": {"type": "string"},
                },
                "required": ["dataset_id", "document_id"],
            },
        ))

        result = await tool(
            dataset_id="set-1", document_id="doc/1?raw=true", view="metadata"
        )

        assert result.success is True
        assert seen["url"].endswith("/datasets/set-1/documents/doc%2F1%3Fraw%3Dtrue")
        assert seen["request_kwargs"]["params"] == {"view": "metadata"}

    async def test_wrong_native_type_fails_before_http(self, monkeypatch):
        called = False

        class _Client:
            def __init__(self, *args, **kwargs):
                nonlocal called
                called = True

        monkeypatch.setattr("tools.custom.http_tool.httpx.AsyncClient", _Client)
        tool = HttpTool(HttpToolConfig(
            name="typed",
            description="typed",
            permission="auto",
            endpoint="http://10.0.0.1/x",
            input_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        ))

        result = await tool(count="3")

        assert result.success is False
        assert "integer" in result.error
        assert called is False

    async def test_internal_endpoint_allowed_and_metadata_sanitized(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "tools.custom.http_tool.httpx.AsyncClient", _client_class(seen)
        )
        tool = HttpTool(HttpToolConfig(
            name="probe",
            description="probe",
            permission="auto",
            endpoint="http://172.22.80.35/data?api_key=SUPERSECRET",
            input_schema=EMPTY_SCHEMA,
        ))

        result = await tool()

        assert result.success is True
        assert result.metadata == {"status_code": 200}

    async def test_response_extract_and_text_artifact(self, monkeypatch):
        seen = {}
        response = _FakeResponse(payload={"data": {"csv": "city,temp\nParis,18"}})
        monkeypatch.setattr(
            "tools.custom.http_tool.httpx.AsyncClient",
            _client_class(seen, response),
        )
        artifact_output = normalize_artifact_output_config(
            {
                "enabled": True,
                "mode": "text",
                "content_type": "text/csv",
                "filename": "weather.csv",
            },
            response_extract="data.csv",
        )
        tool = HttpTool(HttpToolConfig(
            name="weather",
            description="weather",
            permission="auto",
            endpoint="http://10.0.0.1/weather",
            input_schema=EMPTY_SCHEMA,
            response_extract="data.csv",
            artifact_output=artifact_output,
        ))

        result = await tool()

        assert result.success is True
        assert result.artifact.content == "city,temp\nParis,18"
        assert result.artifact.filename == "weather.csv"


class TestDefinitionValidation:
    def test_response_extract_is_jmespath(self):
        validate_response_extract("results[*].id")
        with pytest.raises(ValueError, match="invalid JMESPath"):
            validate_response_extract("$.data.price")

    def test_url_path_rejects_host_and_non_scalar_properties(self):
        string_schema = {
            "type": "object",
            "properties": {"host": {"type": "string"}},
            "required": ["host"],
        }
        with pytest.raises(ValueError, match="only allowed in the path"):
            validate_url_path_template("https://{host}/items", string_schema)

        object_schema = {
            "type": "object",
            "properties": {"filters": {"type": "object"}},
            "required": ["filters"],
        }
        with pytest.raises(ValueError, match="scalar"):
            validate_url_path_template(
                "https://api.example.com/{filters}", object_schema
            )

    def test_filename_star_wins(self):
        assert filename_from_headers({
            "content-disposition": (
                'attachment; filename="report.docx"; '
                "filename*=UTF-8''%E6%8A%A5%E5%91%8A.docx"
            )
        }) == "报告.docx"

    def test_native_query_encoding_is_json_not_python_repr(self):
        # Guard the wire shape explicitly because dict repr is tempting here.
        assert json.dumps({"b": 1, "a": 2}, sort_keys=True, separators=(",", ":")) == (
            '{"a":2,"b":1}'
        )
