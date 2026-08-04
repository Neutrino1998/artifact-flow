"""Native tool input-schema helpers.

The business schema is the single source of truth for both model disclosure
and runtime validation. Native export deep-copies it without adding control
properties, so whole-object JSON Schema constraints retain identical meaning.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError


NATIVE_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
NATIVE_TOOL_NAME_RE = re.compile(NATIVE_TOOL_NAME_PATTERN)
class InputSchemaError(ValueError):
    """A tool definition does not provide a usable business input schema."""


class ToolArgumentError(ValueError):
    """Decoded native arguments do not satisfy a tool's business schema."""


def validate_native_tool_name(name: str) -> None:
    if not isinstance(name, str) or not NATIVE_TOOL_NAME_RE.fullmatch(name):
        raise InputSchemaError(
            f"tool name {name!r} must match {NATIVE_TOOL_NAME_PATTERN}"
        )


def normalize_business_input_schema(
    schema: Mapping[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """Return a validated deep copy of an object-root business schema.

    ArtifactFlow function calls always decode to one arguments object. Nested
    properties and the root object may use the full JSON Schema vocabulary.
    """
    if not isinstance(schema, Mapping):
        raise InputSchemaError(f"{source}: input_schema must be an object")

    normalized = copy.deepcopy(dict(schema))
    if normalized.get("type") != "object":
        raise InputSchemaError(f"{source}: input_schema root type must be 'object'")

    properties = normalized.get("properties", {})
    if not isinstance(properties, dict):
        raise InputSchemaError(f"{source}: input_schema.properties must be an object")
    normalized["properties"] = properties

    required = normalized.get("required", [])
    if not isinstance(required, list) or any(not isinstance(v, str) for v in required):
        raise InputSchemaError(f"{source}: input_schema.required must be an array of strings")
    if len(required) != len(set(required)):
        raise InputSchemaError(f"{source}: input_schema.required contains duplicates")

    try:
        Draft202012Validator.check_schema(normalized)
    except Exception as exc:
        raise InputSchemaError(f"{source}: invalid JSON Schema: {exc}") from exc

    for name, property_schema in properties.items():
        if not isinstance(property_schema, Mapping) or "default" not in property_schema:
            continue
        try:
            Draft202012Validator(property_schema).validate(property_schema["default"])
        except ValidationError as exc:
            raise InputSchemaError(
                f"{source}: default for property '{name}' does not satisfy its schema: "
                f"{exc.message}"
            ) from exc
    return normalized


def build_native_function_schema(
    *,
    name: str,
    description: str,
    business_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an OpenAI-compatible function tool without mutating its source."""
    validate_native_tool_name(name)
    parameters = normalize_business_input_schema(
        business_schema, source=f"tool {name!r}"
    )
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def apply_top_level_defaults(
    schema: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(arguments)
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return result
    for name, property_schema in properties.items():
        if (
            name not in result
            and isinstance(property_schema, Mapping)
            and "default" in property_schema
        ):
            result[name] = copy.deepcopy(property_schema["default"])
    return result


def validate_business_arguments(
    schema: Mapping[str, Any], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply top-level defaults and validate already-decoded native arguments."""
    if not isinstance(arguments, Mapping):
        raise ToolArgumentError("Tool arguments must decode to a JSON object")
    prepared = apply_top_level_defaults(schema, arguments)
    try:
        Draft202012Validator(schema).validate(prepared)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        prefix = f"Parameter {path!r}" if path else "Parameters"
        raise ToolArgumentError(f"{prefix} failed input schema validation: {exc.message}") from exc
    return prepared
