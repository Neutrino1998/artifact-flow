"""MCP tool adapter tests(F-2M-a)."""

from tools.custom.mcp_client import McpClientManager, McpToolCallResult
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
