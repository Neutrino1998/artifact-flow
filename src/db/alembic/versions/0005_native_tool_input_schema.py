"""migrate tool definitions to native JSON Schema

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31

Convert persisted HTTP member definitions from the legacy parameter list to a
single object-root JSON Schema.  Runtime code after this revision reads only
``input_schema``.  Also align stored callable names with the native provider's
64-character function-name limit.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence, Union

from alembic import op
from jsonschema import Draft202012Validator, ValidationError
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NATIVE_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _as_definition(raw: Any, full_name: str) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise RuntimeError(f"tool {full_name!r} has a non-object definition")
    return dict(raw)


def _parameters_to_schema(parameters: Any, full_name: str) -> dict[str, Any]:
    if not isinstance(parameters, list):
        raise RuntimeError(f"tool {full_name!r} has a non-array parameters definition")

    properties: dict[str, Any] = {}
    required: list[str] = []
    for raw in parameters:
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"tool {full_name!r} has a non-object parameter")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"tool {full_name!r} has a parameter without a name")
        if name in properties:
            raise RuntimeError(f"tool {full_name!r} repeats parameter {name!r}")

        legacy_type = raw.get("type", "string")
        if legacy_type == "json":
            schema_type: Any = ["object", "array"]
        elif legacy_type in {"string", "integer", "number", "boolean"}:
            schema_type = legacy_type
        else:
            raise RuntimeError(
                f"tool {full_name!r} has unsupported parameter type {legacy_type!r}"
            )
        prop: dict[str, Any] = {"type": schema_type}
        if raw.get("description"):
            prop["description"] = raw["description"]
        if raw.get("default") is not None:
            prop["default"] = raw["default"]
        if raw.get("enum") is not None:
            prop["enum"] = raw["enum"]
        if "default" in prop:
            try:
                Draft202012Validator(prop).validate(prop["default"])
            except ValidationError as exc:
                raise RuntimeError(
                    f"tool {full_name!r} parameter {name!r} has an invalid default: "
                    f"{exc.message}"
                ) from exc
        properties[name] = prop
        if raw.get("required", True):
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _validate_input_schema(schema: Any, full_name: str) -> dict[str, Any]:
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise RuntimeError(
            f"tool {full_name!r} input_schema must have root type 'object'"
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise RuntimeError(f"tool {full_name!r} input_schema properties must be an object")
    required = schema.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(name, str) for name in required)
        or len(set(required)) != len(required)
    ):
        raise RuntimeError(
            f"tool {full_name!r} input_schema required must contain unique strings"
        )
    try:
        Draft202012Validator.check_schema(dict(schema))
    except Exception as exc:
        raise RuntimeError(
            f"tool {full_name!r} has invalid input_schema: {exc}"
        ) from exc
    for name, property_schema in properties.items():
        if isinstance(property_schema, Mapping) and "default" in property_schema:
            try:
                Draft202012Validator(property_schema).validate(
                    property_schema["default"]
                )
            except ValidationError as exc:
                raise RuntimeError(
                    f"tool {full_name!r} property {name!r} has an invalid default: "
                    f"{exc.message}"
                ) from exc
    return dict(schema)


def _schema_to_parameters(schema: Any, full_name: str) -> list[dict[str, Any]]:
    """Reverse only schemas representable by the removed legacy format."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise RuntimeError(f"cannot downgrade non-object schema for tool {full_name!r}")
    if set(schema) - {"type", "properties", "required", "additionalProperties"}:
        raise RuntimeError(f"cannot downgrade advanced schema for tool {full_name!r}")
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise RuntimeError(f"cannot downgrade invalid schema for tool {full_name!r}")
    required = set(schema.get("required") or [])
    parameters: list[dict[str, Any]] = []
    for name, raw in properties.items():
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"cannot downgrade property {name!r} for tool {full_name!r}")
        if set(raw) - {"type", "description", "default", "enum"}:
            raise RuntimeError(f"cannot downgrade advanced property {name!r} for tool {full_name!r}")
        raw_type = raw.get("type", "string")
        if raw_type == ["object", "array"]:
            legacy_type = "json"
        elif isinstance(raw_type, str) and raw_type in {
            "string", "integer", "number", "boolean"
        }:
            legacy_type = raw_type
        else:
            raise RuntimeError(f"cannot downgrade property {name!r} for tool {full_name!r}")
        parameter = {
            "name": name,
            "type": legacy_type,
            "description": raw.get("description", ""),
            "required": name in required,
            "default": raw.get("default"),
            "enum": raw.get("enum"),
        }
        parameters.append(parameter)
    return parameters


def _rewrite_definitions(*, upgrade_to_schema: bool) -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    members = sa.Table("tool_members", meta, autoload_with=bind)
    rows = list(
        bind.execute(
            sa.select(members.c.unit_name, members.c.member_name, members.c.full_name, members.c.definition)
        ).mappings()
    )
    for row in rows:
        definition = _as_definition(row["definition"], row["full_name"])
        if upgrade_to_schema:
            if "input_schema" in definition and "parameters" in definition:
                raise RuntimeError(
                    f"tool {row['full_name']!r} contains both parameters and input_schema"
                )
            if "parameters" not in definition:
                if "input_schema" in definition:
                    definition["input_schema"] = _validate_input_schema(
                        definition["input_schema"], row["full_name"]
                    )
                    continue
                raise RuntimeError(
                    f"tool {row['full_name']!r} has neither parameters nor input_schema"
                )
            definition["input_schema"] = _parameters_to_schema(
                definition.pop("parameters"), row["full_name"]
            )
            definition["input_schema"] = _validate_input_schema(
                definition["input_schema"], row["full_name"]
            )
        else:
            if "input_schema" not in definition:
                continue
            definition["parameters"] = _schema_to_parameters(
                definition.pop("input_schema"), row["full_name"]
            )
        bind.execute(
            members.update()
            .where(
                sa.and_(
                    members.c.unit_name == row["unit_name"],
                    members.c.member_name == row["member_name"],
                )
            )
            .values(definition=definition)
        )


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    members = sa.Table("tool_members", meta, autoload_with=bind)
    invalid_name = next(
        (
            name
            for name in bind.execute(sa.select(members.c.full_name)).scalars()
            if not _NATIVE_TOOL_NAME_RE.fullmatch(name)
        ),
        None,
    )
    if invalid_name is not None:
        raise RuntimeError(
            "cannot migrate native tool calls: stored full_name "
            f"{invalid_name!r} must match ^[A-Za-z0-9_-]{{1,64}}$"
        )

    _rewrite_definitions(upgrade_to_schema=True)
    with op.batch_alter_table("tool_members") as batch_op:
        batch_op.alter_column(
            "full_name",
            existing_type=sa.String(length=130),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_tool_members_full_name_length",
            "length(full_name) <= 64",
        )


def downgrade() -> None:
    _rewrite_definitions(upgrade_to_schema=False)
    with op.batch_alter_table("tool_members") as batch_op:
        batch_op.drop_constraint(
            "ck_tool_members_full_name_length",
            type_="check",
        )
        batch_op.alter_column(
            "full_name",
            existing_type=sa.String(length=64),
            type_=sa.String(length=130),
            existing_nullable=False,
        )
