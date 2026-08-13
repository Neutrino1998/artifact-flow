"""User-facing projection for execution-event payloads.

Raw events are retained in the stream transport and MessageEvent storage for
admin observability and prompt reconstruction.  Ordinary-user APIs must expose
only the public shape of event types that carry internal execution context.
"""

import re
from typing import Any, Dict

from api.admin_privacy import project_admin_uploaded_files
from config import config
from core.execution.events import StreamEventType


_UPLOAD_HINT_RE = re.compile(
    r"\[The user attached (\d+) file\(s\) to this message: [^\n]*?\. "
    r"Use read_artifact with the id for full content\.\]"
)


def project_event_data_for_user(event_type: str, data: Any) -> Any:
    """Return the ordinary-user view of one event payload.

    ``agent_start`` is allowlisted rather than having known-sensitive fields
    removed.  That keeps future internal context fields private by default.
    Other event types preserve their existing public contract; production error
    text remains sanitized exactly as it was on the replay endpoint.
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

    if event_type == StreamEventType.METADATA.value and "uploaded_files" in data:
        return {
            **data,
            "uploaded_files": project_admin_uploaded_files(data.get("uploaded_files")),
        }

    if event_type == StreamEventType.USER_INPUT.value:
        content = data.get("content")
        if isinstance(content, str):
            return {
                **data,
                "content": _UPLOAD_HINT_RE.sub(
                    r"[The user attached \1 protected file(s) to this message.]",
                    content,
                ),
            }

    if (
        event_type == StreamEventType.ARTIFACT_CREATED.value
        and data.get("source") == "user_upload"
    ):
        redacted = {
            **data,
            "id": "__redacted_upload__",
            "title": "上传文件",
        }
        redacted.pop("original_filename", None)
        return redacted

    return data


def project_event_for_admin(event: Dict[str, Any]) -> Dict[str, Any]:
    """Project a live admin event without mutating the buffered transport value."""
    return {
        **event,
        "data": project_event_data_for_admin(
            str(event.get("type", "")), event.get("data")
        ),
    }
