"""Observer-specific projection for execution-event payloads.

Ordinary-user APIs expose only the public shape of internal execution events.
Admin observability retains semantic diagnostic events, while the optional
privacy boundary suppresses direct artifact live transports.
"""

import re
from typing import Any, Dict, Optional

from api.admin_privacy import (
    project_admin_referenced_artifacts,
    project_admin_uploaded_files,
)
from config import config
from core.execution.events import StreamEventType


_UPLOAD_HINT_RE = re.compile(
    r"\[The user attached (\d+) file\(s\) to this message: [^\n]*?\. "
    r"Use read_artifact with the id for full content\.\]"
)
_REFERENCE_HINT_RE = re.compile(
    r"\[The user explicitly referenced (\d+) existing uploaded file\(s\) for this "
    r"request: [^\n]*?\. Prioritize these files when answering and use read_artifact "
    r"with the ids for full content\. Other session artifacts remain available\.\]"
)


def project_event_data_for_user(event_type: str, data: Any) -> Any:
    """Return the ordinary-user view of one event payload.

    ``agent_start`` is allowlisted rather than having known-sensitive fields
    removed.  That keeps future internal context fields private by default.
    Other event types preserve their existing public contract; production error
    text remains sanitized exactly as it was on the replay endpoint. PAT scopes
    gate API capabilities, not content copied between same-user resources:
    conversation observers intentionally receive complete event payloads even
    when they cannot call the Artifact REST API directly.
    """
    if event_type == StreamEventType.AGENT_START.value:
        if not isinstance(data, dict):
            return {}
        agent = data.get("agent")
        return {"agent": agent} if agent is not None else {}

    if (
        not config.DEBUG
        and event_type == StreamEventType.ERROR.value
        and isinstance(data, dict)
        and data.get("error")
    ):
        return {**data, "error": "Internal server error"}

    return data


def project_event_for_user(event: Dict[str, Any]) -> Dict[str, Any]:
    """Project a live event without mutating the transport's buffered object."""
    return {
        **event,
        "data": project_event_data_for_user(
            str(event.get("type", "")), event.get("data")
        ),
    }


def project_event_data_for_admin(event_type: str, data: Any) -> Any:
    """Remove direct upload names from admin events when privacy mode is active."""
    if not config.ADMIN_PRIVACY_MODE or not isinstance(data, dict):
        return data

    if event_type == StreamEventType.METADATA.value:
        projected = dict(data)
        if "uploaded_files" in data:
            projected["uploaded_files"] = project_admin_uploaded_files(
                data.get("uploaded_files")
            )
        if "referenced_artifacts" in data:
            projected["referenced_artifacts"] = project_admin_referenced_artifacts(
                data.get("referenced_artifacts")
            )
        return projected

    if event_type == StreamEventType.USER_INPUT.value:
        content = data.get("content")
        if isinstance(content, str):
            return {
                **data,
                "content": _REFERENCE_HINT_RE.sub(
                    r"[The user referenced \1 protected file(s) for this request.]",
                    _UPLOAD_HINT_RE.sub(
                        r"[The user attached \1 protected file(s) to this message.]",
                        content,
                    ),
                ),
            }

    return data


def project_event_for_admin(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Project one live admin event, or suppress a direct artifact transport."""
    event_type = str(event.get("type", ""))
    if config.ADMIN_PRIVACY_MODE and event_type in {
        StreamEventType.ARTIFACT_CREATED.value,
        StreamEventType.ARTIFACT_UPDATED.value,
    }:
        return None
    return {
        **event,
        "data": project_event_data_for_admin(event_type, event.get("data")),
    }
