"""User-facing projection for execution-event payloads.

Raw events are retained in the stream transport and MessageEvent storage for
admin observability and prompt reconstruction.  Ordinary-user APIs must expose
only the public shape of event types that carry internal execution context.
"""

from typing import Any, Dict

from config import config
from core.events import StreamEventType


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
