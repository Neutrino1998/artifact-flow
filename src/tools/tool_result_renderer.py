"""Model-facing text rendering for native ``role=tool`` messages."""

from __future__ import annotations

from typing import Any, Mapping


def render_tool_result(name: str, result: Mapping[str, Any]) -> str:
    """Render the existing readable result envelope without parsing it later."""
    success = bool(result.get("success", False))
    data = result.get("data") or ""
    error = result.get("error") or ""
    text = f'<tool_result name="{name}" success="{"true" if success else "false"}">'
    if data:
        text += f"\n<data>\n{data}\n</data>"
    if error:
        text += f"\n  <error>{error}</error>"
    return text + "\n</tool_result>"
