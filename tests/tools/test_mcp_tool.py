"""MCP tool adapter tests(F-2M)."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest

from config import config
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


def _tool(manager):
    return McpTool(
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


async def test_mcp_tool_converts_single_image_content_block_to_artifact():
    png = b"\x89PNG\r\n\x1a\n"

    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": base64.b64encode(png).decode("ascii"),
                }
            ],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is True
    assert result.artifact is not None
    assert result.artifact.content_type == "image/png"
    assert result.artifact.blob == png
    assert result.artifact.content == ""
    assert result.artifact.metadata == {
        "artifact_output": True,
        "artifact_output_mode": "binary",
        "mcp_server": "inventory",
        "mcp_tool": "lookup",
        "mcp_content_block_type": "image",
    }


async def test_mcp_tool_converts_embedded_blob_resource_to_artifact():
    payload = b"sheet,data\n1,2\n"

    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///report.csv",
                        "mimeType": "text/csv",
                        "blob": base64.b64encode(payload).decode("ascii"),
                    },
                }
            ],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is True
    assert result.artifact is not None
    assert result.artifact.content_type == "text/csv"
    assert result.artifact.blob == payload
    assert result.artifact.metadata["mcp_content_block_type"] == "resource"
    assert result.artifact.metadata["mcp_resource_uri"] == "file:///report.csv"


async def test_mcp_tool_rejects_mixed_text_and_artifact_blocks():
    payload = base64.b64encode(b"x").decode("ascii")

    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[
                {"type": "text", "text": "generated report"},
                {"type": "image", "mimeType": "image/png", "data": payload},
            ],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is False
    assert result.artifact is None
    assert "mixed text/structured and artifact content" in result.error


async def test_mcp_tool_accepts_empty_text_resource():
    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[
                {
                    "type": "resource",
                    "resource": {
                        "uri": "file:///empty.txt",
                        "mimeType": "text/plain",
                        "text": "",
                    },
                }
            ],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is True
    assert result.data == ""
    assert result.artifact is None


async def test_mcp_tool_rejects_multiple_non_text_artifact_blocks():
    payload = base64.b64encode(b"x").decode("ascii")

    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[
                {"type": "image", "mimeType": "image/png", "data": payload},
                {"type": "audio", "mimeType": "audio/wav", "data": payload},
            ],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is False
    assert result.artifact is None
    assert "multiple artifacts are not supported" in result.error


async def test_mcp_tool_rejects_invalid_base64_content_block():
    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=False,
            content=[{"type": "image", "mimeType": "image/png", "data": "not-base64"}],
        )

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is False
    assert result.artifact is None
    assert "invalid base64" in result.error


async def test_mcp_list_cache_uses_ttl(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOOL_LIST_CACHE_SECONDS", 60)
    calls = 0

    async def fake_list(url, headers, timeout):
        nonlocal calls
        calls += 1
        return [{"name": f"lookup_{calls}", "description": "", "inputSchema": {}}]

    manager = McpClientManager(list_callable=fake_list)

    first = await manager.list_tools("inventory", _provider_config())
    second = await manager.list_tools("inventory", _provider_config())

    assert [tool.name for tool in first.tools] == ["lookup_1"]
    assert [tool.name for tool in second.tools] == ["lookup_1"]
    assert calls == 1

    monkeypatch.setattr(config, "MCP_TOOL_LIST_CACHE_SECONDS", 0)
    third = await manager.list_tools("inventory", _provider_config())

    assert [tool.name for tool in third.tools] == ["lookup_2"]
    assert calls == 2


async def test_mcp_call_error_invalidates_list_cache(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOOL_LIST_CACHE_SECONDS", 60)
    list_calls = 0

    async def fake_list(url, headers, timeout):
        nonlocal list_calls
        list_calls += 1
        return [{"name": f"lookup_{list_calls}", "description": "", "inputSchema": {}}]

    async def fake_call(url, headers, timeout, tool_name, arguments):
        return McpToolCallResult(
            is_error=True,
            content=[{"type": "text", "text": "unknown tool"}],
        )

    manager = McpClientManager(list_callable=fake_list, call_callable=fake_call)

    first = await manager.list_tools("inventory", _provider_config())
    await manager.call_tool("inventory", _provider_config(), "lookup", {})
    second = await manager.list_tools("inventory", _provider_config())

    assert [tool.name for tool in first.tools] == ["lookup_1"]
    assert [tool.name for tool in second.tools] == ["lookup_2"]
    assert list_calls == 2


async def test_mcp_list_internal_cancel_degrades_to_unavailable():
    async def fake_list(_url, _headers, _timeout):
        raise asyncio.CancelledError()

    manager = McpClientManager(list_callable=fake_list)

    result = await manager.list_tools("inventory", _provider_config())

    assert result.tools == []
    assert result.error == "MCP server is unavailable"


async def test_mcp_list_cleanup_exception_group_degrades_to_unavailable():
    async def fake_list(_url, _headers, _timeout):
        raise BaseExceptionGroup(
            "stream cleanup failed",
            [
                RuntimeError("Attempted to exit cancel scope in a different task"),
                GeneratorExit(),
            ],
        )

    manager = McpClientManager(list_callable=fake_list)

    result = await manager.list_tools("inventory", _provider_config())

    assert result.tools == []
    assert result.error == "MCP server is unavailable"


async def test_mcp_call_internal_cancel_returns_tool_error():
    async def fake_call(_url, _headers, _timeout, _tool_name, _arguments):
        raise asyncio.CancelledError()

    result = await _tool(McpClientManager(call_callable=fake_call))()

    assert result.success is False
    assert "could not reach the server" in result.error


async def test_mcp_outer_cancel_still_propagates():
    async def fake_list(_url, _headers, _timeout):
        await asyncio.sleep(60)

    manager = McpClientManager(list_callable=fake_list)
    task = asyncio.create_task(manager.list_tools("inventory", _provider_config()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_mcp_outer_cancel_with_cleanup_error_still_propagates():
    entered = asyncio.Event()

    async def fake_list(_url, _headers, _timeout):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            await asyncio.sleep(0)
            raise RuntimeError("cleanup failed")

    manager = McpClientManager(list_callable=fake_list)
    task = asyncio.create_task(manager.list_tools("inventory", _provider_config()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


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


async def test_sdk_session_suppresses_nonfatal_transport_cleanup_error():
    seen = {}

    @asynccontextmanager
    async def fake_transport(_url, *, http_client):
        try:
            yield "read-stream", "write-stream", lambda: "session-id"
        finally:
            raise RuntimeError("Attempted to exit cancel scope in a different task")

    class FakeSession:
        def __init__(self, _read_stream, _write_stream, *, read_timeout_seconds):
            seen["read_timeout_seconds"] = read_timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def initialize(self):
            seen["initialized"] = True

    async with _McpSessionContext(
        "https://mcp.example.com/inventory",
        {},
        15,
        FakeSession,
        fake_transport,
    ):
        seen["inside"] = True

    assert seen == {
        "read_timeout_seconds": timedelta(seconds=15),
        "initialized": True,
        "inside": True,
    }


async def test_sdk_session_outer_cancel_with_cleanup_error_still_propagates():
    @asynccontextmanager
    async def fake_transport(_url, *, http_client):
        try:
            yield "read-stream", "write-stream", lambda: "session-id"
        finally:
            raise RuntimeError("Attempted to exit cancel scope in a different task")

    class FakeSession:
        def __init__(self, _read_stream, _write_stream, *, read_timeout_seconds):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def initialize(self):
            pass

    async def use_session():
        async with _McpSessionContext(
            "https://mcp.example.com/inventory",
            {},
            15,
            FakeSession,
            fake_transport,
        ):
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

    with pytest.raises(asyncio.CancelledError):
        await use_session()


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
