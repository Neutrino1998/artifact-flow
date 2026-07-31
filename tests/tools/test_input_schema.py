import copy

import pytest

from tools.base import BaseTool, ToolResult
from tools.input_schema import (
    InputSchemaError,
    build_native_function_schema,
    normalize_business_input_schema,
    validate_native_tool_name,
)


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "options": {
            "type": "object",
            "properties": {
                "mode": {"enum": ["fast", "deep"]},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                },
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        "limit": {"type": "integer", "minimum": 1, "default": 10},
    },
    "required": ["query", "options"],
    "additionalProperties": False,
}


class _RecordingTool(BaseTool):
    def __init__(self):
        super().__init__("lookup", "Lookup things")
        self.received = None

    def get_input_schema(self):
        return SCHEMA

    async def execute(self, **params):
        self.received = params
        return ToolResult(success=True, data="ok")


def test_native_export_injects_required_reason_without_mutating_business_schema():
    original = copy.deepcopy(SCHEMA)

    native = build_native_function_schema(
        name="lookup", description="Lookup things", business_schema=SCHEMA
    )

    assert SCHEMA == original
    parameters = native["function"]["parameters"]
    assert list(parameters["properties"])[0] == "__reason"
    assert parameters["required"] == ["__reason", "query", "options"]
    assert parameters["properties"]["options"] == SCHEMA["properties"]["options"]


async def test_runtime_keeps_native_types_applies_defaults_and_validates_nested_schema():
    tool = _RecordingTool()

    result = await tool(query="weather", options={"mode": "deep", "tags": ["a"]})

    assert result.success is True
    assert tool.received == {
        "query": "weather",
        "options": {"mode": "deep", "tags": ["a"]},
        "limit": 10,
    }

    bad = await tool(query="weather", options={"mode": "deep", "tags": ["a", "a"]})
    assert bad.success is False
    assert "unique" in bad.error


@pytest.mark.parametrize(
    "schema, message",
    [
        ({"type": "array"}, "root type"),
        ({"type": "object", "properties": {"__reason": {"type": "string"}}}, "reserved"),
        ({"type": "object", "properties": {}, "required": ["missing"]}, "not declared"),
        (
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": "ten"}},
            },
            "default",
        ),
    ],
)
def test_invalid_business_schemas_fail_at_definition_boundary(schema, message):
    with pytest.raises(InputSchemaError, match=message):
        normalize_business_input_schema(schema, source="test")


@pytest.mark.parametrize("name", ["ok", "unit__member", "A-1", "x" * 64])
def test_native_tool_name_accepts_wire_safe_names(name):
    validate_native_tool_name(name)


@pytest.mark.parametrize("name", ["", "has space", "bad.dot", "x" * 65])
def test_native_tool_name_rejects_invalid_names(name):
    with pytest.raises(InputSchemaError):
        validate_native_tool_name(name)
