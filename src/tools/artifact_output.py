"""Helpers for tool-declared artifact output.

This module is intentionally pure: it validates/normalizes configuration and
builds ``ArtifactSpec`` objects, but never imports or touches ArtifactService.
"""

import mimetypes
import os
from typing import Any, Dict, Optional

from tools.base import ArtifactSpec

_VALID_MODES = {"text", "binary"}


def normalize_artifact_output_config(
    raw: Optional[Dict[str, Any]],
    *,
    response_extract: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Normalize a tool member's ``artifact_output`` config.

    Disabled/missing configs normalize to ``None`` so existing no-artifact tools
    keep their stored definition shape small. Enabled configs are returned as a
    stable dict suitable for DB storage and snapshot reconstruction.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("artifact_output must be an object")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("artifact_output.enabled must be a boolean")
    if not enabled:
        return None

    mode = raw.get("mode", "text")
    if mode not in _VALID_MODES:
        raise ValueError("artifact_output.mode must be 'text' or 'binary'")

    content_type = _optional_str(raw, "content_type")
    filename = _optional_str(raw, "filename")
    title = _optional_str(raw, "title")

    if mode == "text":
        content_type = content_type or "text/plain"
    else:
        if response_extract:
            raise ValueError("artifact_output.mode='binary' cannot be combined with response_extract")
        if not content_type:
            raise ValueError("artifact_output.content_type is required for binary mode")

    return {
        "enabled": True,
        "mode": mode,
        "content_type": content_type,
        "filename": filename,
        "title": title,
    }


def build_artifact_spec(
    *,
    tool_name: str,
    config: Dict[str, Any],
    text: Optional[str] = None,
    blob: Optional[bytes] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ArtifactSpec:
    """Build an ``ArtifactSpec`` from normalized artifact-output config."""
    mode = config.get("mode", "text")
    content_type = config.get("content_type") or (
        "text/plain" if mode == "text" else "application/octet-stream"
    )
    filename = config.get("filename") or _default_filename(tool_name, mode, content_type)
    title = config.get("title") or os.path.splitext(filename)[0]
    meta = {
        "artifact_output": True,
        "artifact_output_mode": mode,
        **(metadata or {}),
    }

    if mode == "binary":
        if blob is None:
            raise ValueError("binary artifact output requires blob bytes")
        return ArtifactSpec(
            content_type=content_type,
            filename=filename,
            title=title,
            content="",
            blob=blob,
            metadata=meta,
        )

    if text is None:
        raise ValueError("text artifact output requires text content")
    return ArtifactSpec(
        content_type=content_type,
        filename=filename,
        title=title,
        content=text,
        blob=None,
        metadata=meta,
    )


def _optional_str(raw: Dict[str, Any], key: str) -> Optional[str]:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"artifact_output.{key} must be a string")
    stripped = value.strip()
    return stripped or None


def _default_filename(tool_name: str, mode: str, content_type: str) -> str:
    extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or (
        ".txt" if mode == "text" else ".bin"
    )
    return f"{tool_name}_output{extension}"
