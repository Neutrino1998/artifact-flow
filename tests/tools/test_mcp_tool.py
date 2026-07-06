"""MCP tool adapter tests(F-2M-a)."""

from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

from tools.custom.mcp_client import McpClientManager, McpToolCallResult
from tools.custom.mcp_client import _McpSessionContext
from tools.custom.mcp_tool import McpTool, McpToolConfig, parameters_from_json_schema


def _provider_config():
    return {
        "transport": "streamable_http",
        "url": "https://mcp.example.com/inventory",
        "headers": {"X-Tenant": "ops"},
        "timeout": 15,
        "default_permission": "confirm",
    }


def _schema():
    return {
        "type": "object",
        "properties": {
            "sku": {"type": "string", "description": "SKU", "enum": ["A-1", "B-2"]},
            "limit": {"type": "integer", "default": 5},
            "filters": {
                "type": "object",
                "description": "Additional filters",
                "properties": {"warehouse": {"type": "string"}},
            },
        },
        "required": ["sku"],
    }


def test_parameters_from_json_schema_maps_object_params_to_json_string():
    params = parameters_from_json_schema(_schema())

    assert [(p.name, p.type, p.required) for p in params] == [
        ("sku", "string", True),
        ("limit", "integer", False),
        ("filters", "json", False),
    ]
    assert params[0].enum == ["A-1", "B-2"]
    assert params[1].default == 5
    assert "JSON string" in params[2].description


async def test_mcp_tool_calls_manager_with_schema_coerced_arguments():
    seen = {}

    async def fake_call(url, headers, timeout, tool_name, arguments):
        seen.update(
            url=url,
            headers=headers,
            timeout=timeout,
            tool_name=tool_name,
            arguments=arguments,
        )
        return McpToolCallResult(
            is_error=False,
            content=[{"type": "text", "text": "stock: 12"}],
        )

    manager = McpClientManager(call_callable=fake_call)
    tool = McpTool(
        McpToolConfig(
            full_name="inventory__lookup",
            server_name="inventory",
            tool_name="lookup",
            description="Lookup inventory",
            permission="confirm",
            input_schema=_schema(),
            provider_config=_provider_config(),
        ),
        client_manager=manager,
    )

    result = await tool(sku="A-1", filters='{"warehouse":"east"}')

    assert result.success is True
    assert result.data == "stock: 12"
    assert seen == {
        "url": "https://mcp.example.com/inventory",
        "headers": {"X-Tenant": "ops"},
        "timeout": 15,
        "tool_name": "lookup",
        "arguments": {"sku": "A-1", "limit": 5, "filters": {"warehouse": "east"}},
    }


async def test_mcp_tool_rejects_invalid_json_object_param_before_call():
    called = False

    async def fake_call(url, headers, timeout, tool_name, arguments):
        nonlocal called
        called = True
        return McpToolCallResult(is_error=False, content=[])

    manager = McpClientManager(call_callable=fake_call)
    tool = McpTool(
        McpToolConfig(
            full_name="inventory__lookup",
            server_name="inventory",
            tool_name="lookup",
            description="Lookup inventory",
            permission="confirm",
            input_schema=_schema(),
            provider_config=_provider_config(),
        ),
        client_manager=manager,
    )

    result = await tool(sku="A-1", filters="{not-json")

    assert result.success is False
    assert "filters" in result.error
    assert called is False


async def test_mcp_tool_renders_structured_content_as_json():
    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[],
            structured_content={"ok": True, "items": [1, 2]},
        )

    manager = McpClientManager(call_callable=fake_call)
    tool = McpTool(
        McpToolConfig(
            full_name="inventory__lookup",
            server_name="inventory",
            tool_name="lookup",
            description="Lookup inventory",
            permission="confirm",
            input_schema={"type": "object", "properties": {}},
            provider_config=_provider_config(),
        ),
        client_manager=manager,
    )

    result = await tool()

    assert result.success is True
    assert result.data == '{"ok": true, "items": [1, 2]}'


async def test_sdk_session_accepts_streamable_http_triple_and_uses_timedelta_timeout():
    seen = {}

    @asynccontextmanager
    async def fake_transport(url, *, http_client):
        seen["url"] = url
        seen["headers"] = dict(http_client.headers)
        yield "read-stream", "write-stream", lambda: "session-id"

    class FakeSession:
        def __init__(self, read_stream, write_stream, *, read_timeout_seconds):
            seen["read_stream"] = read_stream
            seen["write_stream"] = write_stream
            seen["read_timeout_seconds"] = read_timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def initialize(self):
            seen["initialized"] = True

    async with _McpSessionContext(
        "https://mcp.example.com/inventory",
        {"X-Tenant": "ops"},
        15,
        FakeSession,
        fake_transport,
    ) as session:
        assert isinstance(session, FakeSession)

    assert seen["url"] == "https://mcp.example.com/inventory"
    assert seen["read_stream"] == "read-stream"
    assert seen["write_stream"] == "write-stream"
    assert seen["read_timeout_seconds"] == timedelta(seconds=15)
    assert seen["initialized"] is True


async def test_sdk_call_reads_camel_case_result_fields_and_uses_timedelta_timeout():
    seen = {}

    class FakeSession:
        async def call_tool(self, tool_name, arguments, *, read_timeout_seconds):
            seen["tool_name"] = tool_name
            seen["arguments"] = arguments
            seen["read_timeout_seconds"] = read_timeout_seconds
            return SimpleNamespace(
                isError=True,
                content=[{"type": "text", "text": "bad"}],
                structuredContent={"error": "bad"},
            )

    class FakeSessionContext:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    manager = McpClientManager()
    manager._sdk_session = lambda url, headers, timeout: FakeSessionContext()

    result = await manager._call_tool_via_sdk(
        "https://mcp.example.com/inventory",
        {"X-Tenant": "ops"},
        15,
        "lookup",
        {"sku": "A-1"},
    )

    assert seen == {
        "tool_name": "lookup",
        "arguments": {"sku": "A-1"},
        "read_timeout_seconds": timedelta(seconds=15),
    }
    assert result.is_error is True
    assert result.structured_content == {"error": "bad"}
