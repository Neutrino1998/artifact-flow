"""Helpers for tool-declared artifact output.

This module is intentionally pure: it validates/normalizes configuration and
builds ``ArtifactSpec`` objects, but never imports or touches ArtifactService.
"""

import mimetypes
import os
import re
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Any, Dict, Mapping, Optional

from tools.base import ArtifactSpec

_VALID_MODES = {"text", "binary"}
MAX_CONTENT_TYPE_LENGTH = 128
MAX_TITLE_LENGTH = 256
MAX_FILENAME_LENGTH = 256
_MIME_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


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

    content_type = _optional_str(raw, "content_type", max_length=MAX_CONTENT_TYPE_LENGTH)
    filename = _optional_str(raw, "filename", max_length=MAX_FILENAME_LENGTH)
    title = _optional_str(raw, "title", max_length=MAX_TITLE_LENGTH)

    if mode == "text":
        content_type = content_type or "text/plain"
    else:
        if response_extract:
            raise ValueError("artifact_output.mode='binary' cannot be combined with response_extract")

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
    content_type_override: Optional[str] = None,
    filename_override: Optional[str] = None,
) -> ArtifactSpec:
    """Build an ``ArtifactSpec`` from normalized artifact-output config."""
    mode = config.get("mode", "text")
    content_type = config.get("content_type") or content_type_override or (
        "text/plain" if mode == "text" else "application/octet-stream"
    )
    filename = config.get("filename") or filename_override or _default_filename(tool_name, mode, content_type)
    title = config.get("title") or os.path.splitext(filename)[0]
    if len(content_type) > MAX_CONTENT_TYPE_LENGTH:
        raise ValueError(f"artifact_output.content_type must be <= {MAX_CONTENT_TYPE_LENGTH} characters")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise ValueError(f"artifact_output.filename must be <= {MAX_FILENAME_LENGTH} characters")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"artifact_output.title must be <= {MAX_TITLE_LENGTH} characters")
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


def content_type_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    """Return a safe MIME type from response headers, or ``None``."""
    raw = _header_value(headers, "content-type")
    if not raw:
        return None
    candidate = raw.split(";", 1)[0].strip().lower()
    if len(candidate) > MAX_CONTENT_TYPE_LENGTH:
        return None
    if not _MIME_TYPE_PATTERN.fullmatch(candidate):
        return None
    return candidate


def filename_from_headers(headers: Mapping[str, str]) -> Optional[str]:
    """Return a safe filename from Content-Disposition, or ``None``."""
    raw = _header_value(headers, "content-disposition")
    if not raw:
        return None

    message = Message()
    message["content-disposition"] = raw
    fallback: Optional[str] = None
    for name, value in message.get_params(header="content-disposition") or []:
        if name.lower() != "filename":
            continue
        if isinstance(value, tuple):
            filename = _safe_filename(collapse_rfc2231_value(value))
            if filename:
                return filename
        elif fallback is None:
            fallback = _safe_filename(str(value))
    return fallback


def _optional_str(raw: Dict[str, Any], key: str, *, max_length: int) -> Optional[str]:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"artifact_output.{key} must be a string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise ValueError(f"artifact_output.{key} must be <= {max_length} characters")
    return stripped or None


def _header_value(headers: Mapping[str, str], name: str) -> Optional[str]:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    if value is None:
        value = headers.get(name.title())
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_filename(filename: str) -> Optional[str]:
    cleaned = "".join(ch for ch in filename.strip() if ch >= " " and ch != "\x7f")
    cleaned = os.path.basename(cleaned.replace("\\", "/"))
    if not cleaned or cleaned in {".", ".."}:
        return None
    if len(cleaned) > MAX_FILENAME_LENGTH:
        return None
    return cleaned


def _default_filename(tool_name: str, mode: str, content_type: str) -> str:
    extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or (
        ".txt" if mode == "text" else ".bin"
    )
    return f"{tool_name}_output{extension}"
