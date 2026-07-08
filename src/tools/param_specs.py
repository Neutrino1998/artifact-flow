"""Shared validation for tool parameter definitions."""

from typing import Any, Iterable, Mapping

from tools.base import ToolParameter


VALID_PARAM_TYPES = frozenset({"string", "integer", "number", "boolean", "json"})


def normalize_parameter_specs(
    raw_parameters: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Validate and normalize stored/API parameter specs.

    Runtime validation still checks call-time values. This function keeps bad
    tool definitions from reaching storage: especially JSON defaults/enums that
    would only fail later when a tool is called.
    """
    params: list[dict[str, Any]] = []
    for raw in raw_parameters or []:
        if not isinstance(raw, Mapping):
            raise ValueError("parameter spec must be an object")

        name = raw.get("name")
        if not name:
            raise ValueError("parameter missing 'name'")

        ptype = raw.get("type", "string")
        if ptype not in VALID_PARAM_TYPES:
            raise ValueError(
                f"Unsupported parameter type '{ptype}' for '{name}'. "
                f"Valid: {sorted(VALID_PARAM_TYPES)}"
            )

        default = raw.get("default")
        enum = raw.get("enum")
        if ptype == "json":
            _validate_json_default(name, default)
            _validate_json_enum(name, enum)
        elif enum is not None and not isinstance(enum, list):
            raise ValueError(f"parameter '{name}' enum must be a list")

        params.append({
            "name": name,
            "type": ptype,
            "description": raw.get("description", ""),
            "required": raw.get("required", True),
            "default": default,
            "enum": enum,
        })
    return params


def parameter_specs_to_tool_parameters(
    specs: Iterable[Mapping[str, Any]],
) -> list[ToolParameter]:
    return [
        ToolParameter(
            name=p["name"],
            type=p.get("type", "string"),
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
            enum=p.get("enum"),
        )
        for p in specs
    ]


def _validate_json_default(name: str, default: Any) -> None:
    if default is not None and not isinstance(default, (dict, list)):
        raise ValueError(
            f"parameter '{name}' default for json type must be a JSON object or array"
        )


def _validate_json_enum(name: str, enum: Any) -> None:
    if enum is None:
        return
    if not isinstance(enum, list):
        raise ValueError(f"parameter '{name}' enum must be a list")
    for idx, item in enumerate(enum):
        if not isinstance(item, (dict, list)):
            raise ValueError(
                f"parameter '{name}' enum[{idx}] for json type must be a JSON object or array"
            )
