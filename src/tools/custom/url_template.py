"""URL path templating for HTTP custom tools.

``{{NAME}}`` is reserved for credentials. HTTP path parameters use single
braces: ``/datasets/{dataset_id}/documents/{document_id}``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit


_PATH_PARAM_PATTERN = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
_DOUBLE_BRACE_PATTERN = re.compile(r"\{\{\w+\}\}")


class UrlTemplateError(ValueError):
    """Invalid URL path template configuration or call-time values."""


def extract_url_path_params(endpoint: str) -> list[str]:
    """Return unique ``{param}`` names used in an endpoint path template."""
    _validate_endpoint_string(endpoint)
    _validate_brace_syntax(endpoint)
    _validate_placeholders_are_in_path(endpoint)

    seen: set[str] = set()
    names: list[str] = []
    for match in _PATH_PARAM_PATTERN.finditer(endpoint):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def validate_url_path_template(
    endpoint: str,
    parameters: Iterable[Mapping[str, Any]] | None,
) -> None:
    """Validate endpoint ``{param}`` references against declared parameters."""
    refs = extract_url_path_params(endpoint)
    if not refs:
        return

    param_by_name = {str(p.get("name")): p for p in parameters or []}
    for name in refs:
        param = param_by_name.get(name)
        if param is None:
            raise UrlTemplateError(
                f"URL path parameter '{{{name}}}' must reference a declared parameter"
            )
        if param.get("type", "string") == "json":
            raise UrlTemplateError(
                f"URL path parameter '{{{name}}}' cannot use json type"
            )
        default = param.get("default")
        if default == "" or isinstance(default, (dict, list)):
            raise UrlTemplateError(
                f"URL path parameter '{{{name}}}' default must be a non-empty scalar"
            )
        if param.get("required", True) is False and default is None:
            raise UrlTemplateError(
                f"URL path parameter '{{{name}}}' must be required or have a default"
            )


def render_url_path_template(
    endpoint: str,
    params: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Substitute path params and return ``(endpoint, remaining_params)``.

    Values are URL-encoded with ``safe=""`` so a model-supplied value cannot add
    path segments, query strings, or fragments.
    """
    refs = extract_url_path_params(endpoint)
    if not refs:
        return endpoint, dict(params)

    consumed: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise UrlTemplateError(
                f"missing value for URL path parameter '{{{name}}}'"
            )
        value = params[name]
        if value is None or value == "":
            raise UrlTemplateError(
                f"missing value for URL path parameter '{{{name}}}'"
            )
        if isinstance(value, (dict, list)):
            raise UrlTemplateError(
                f"URL path parameter '{{{name}}}' cannot use a JSON object or array"
            )
        consumed.add(name)
        return quote(_stringify_path_value(value), safe="")

    rendered = _PATH_PARAM_PATTERN.sub(_replace, endpoint)
    remaining = {k: v for k, v in params.items() if k not in consumed}
    return rendered, remaining


def _validate_endpoint_string(endpoint: str) -> None:
    if not isinstance(endpoint, str):
        raise UrlTemplateError("endpoint must be a string")


def _validate_brace_syntax(endpoint: str) -> None:
    stripped = _DOUBLE_BRACE_PATTERN.sub("", endpoint)
    stripped = _PATH_PARAM_PATTERN.sub("", stripped)
    if "{" in stripped or "}" in stripped:
        raise UrlTemplateError(
            "invalid URL path template; use {param_name} for path parameters "
            "and {{TOOL_SECRET_NAME}} for credentials"
        )


def _validate_placeholders_are_in_path(endpoint: str) -> None:
    path_start, path_end = _path_bounds(endpoint)
    for match in _PATH_PARAM_PATTERN.finditer(endpoint):
        if not (path_start <= match.start() and match.end() <= path_end):
            raise UrlTemplateError(
                f"URL path parameter '{{{match.group(1)}}}' is only allowed in the path"
            )


def _path_bounds(endpoint: str) -> tuple[int, int]:
    parts = urlsplit(endpoint)
    if parts.scheme and parts.netloc:
        path_start = len(parts.scheme) + 3 + len(parts.netloc)
    else:
        path_start = 0
    return path_start, path_start + len(parts.path)


def _stringify_path_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
