import importlib

import pytest


migration = importlib.import_module(
    "db.alembic.versions.0005_native_tool_input_schema"
)


def test_legacy_parameters_convert_to_object_schema_without_stringifying_json():
    schema = migration._parameters_to_schema(
        [
            {"name": "query", "type": "string", "required": True},
            {
                "name": "payload",
                "type": "json",
                "required": False,
                "default": {"ids": [1, 2]},
            },
        ],
        "lookup",
    )

    assert schema == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "payload": {
                "type": ["object", "array"],
                "default": {"ids": [1, 2]},
            },
        },
        "additionalProperties": False,
        "required": ["query"],
    }


@pytest.mark.parametrize(
    "parameters, message",
    [
        ([{"name": "__reason", "type": "string"}], "reserved"),
        (
            [
                {"name": "x", "type": "string"},
                {"name": "x", "type": "string"},
            ],
            "repeats",
        ),
        (
            [{"name": "payload", "type": "json", "default": "scalar"}],
            "invalid default",
        ),
    ],
)
def test_migration_rejects_ambiguous_or_invalid_legacy_definitions(parameters, message):
    with pytest.raises(RuntimeError, match=message):
        migration._parameters_to_schema(parameters, "bad")


def test_migration_validates_preexisting_native_schema_and_reserved_reason():
    with pytest.raises(RuntimeError, match="reserved"):
        migration._validate_input_schema(
            {
                "type": "object",
                "properties": {"__reason": {"type": "string"}},
            },
            "bad",
        )


@pytest.mark.parametrize(
    "schema, message",
    [
        (
            {
                "type": "object",
                "properties": {"known": {"type": "string"}},
                "required": ["missing"],
            },
            "required",
        ),
        (
            {
                "type": "object",
                "properties": {"count": {"type": "integer", "default": "one"}},
            },
            "invalid default",
        ),
    ],
)
def test_migration_rejects_native_schema_runtime_would_reject(schema, message):
    with pytest.raises(RuntimeError, match=message):
        migration._validate_input_schema(schema, "bad")


def test_downgrade_refuses_advanced_schema_instead_of_losing_information():
    with pytest.raises(RuntimeError, match="advanced"):
        migration._schema_to_parameters(
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
            },
            "advanced",
        )


@pytest.mark.parametrize("name", ["with space", "tool.dot", "", "x" * 65])
def test_migration_native_name_gate_matches_runtime_contract(name):
    assert migration._NATIVE_TOOL_NAME_RE.fullmatch(name) is None
