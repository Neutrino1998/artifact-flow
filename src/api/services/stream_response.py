"""Shared SSE response construction for user and admin observers."""

import asyncio
from typing import AsyncGenerator, Literal, Optional

from starlette.responses import StreamingResponse

from config import config
from api.event_projection import project_event_for_admin, project_event_for_user
from api.services.stream_transport import StreamNotFoundError, StreamTransport
from api.utils.sse import format_sse_comment, format_sse_event
from utils.logger import get_logger, get_request_id
from utils.time import utc_now

logger = get_logger("ArtifactFlow")

_TERMINAL_EVENTS = ("complete", "cancelled", "timed_out", "error")
SSE_OPENAPI_RESPONSES = {
    200: {
        "description": "Server-Sent Events stream",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}


def build_stream_response(
    *,
    stream_id: str,
    stream_transport: StreamTransport,
    user_id: Optional[str],
    last_event_id: Optional[str],
    event_view: Literal["user", "admin"] = "user",
) -> StreamingResponse:
    """Build one observer's SSE response over a producer-owned stream."""

    async def event_generator() -> AsyncGenerator[str, None]:
        consumer = stream_transport.consume_events(
            stream_id,
            heartbeat_interval=config.SSE_PING_INTERVAL,
            user_id=user_id,
            last_event_id=last_event_id,
        )
        try:
            async for event in consumer:
                if event.get("type") == "__ping__":
                    yield format_sse_comment("ping")
                    continue
                if event_view == "user":
                    event = project_event_for_user(event)
                elif event_view == "admin":
                    event = project_event_for_admin(event)
                stream_entry_id = event.pop("_stream_id", None)
                yield format_sse_event(
                    event, event=event.get("type"), id=stream_entry_id
                )
                event_type = event.get("type", "")
                if event_type in _TERMINAL_EVENTS:
                    logger.info(
                        "Stream %s: terminal event %r, closing observer",
                        stream_id,
                        event_type,
                    )
                    break
        except StreamNotFoundError:
            logger.warning("Stream %s: not found or expired", stream_id)
            error_event = {
                "type": "error",
                "timestamp": utc_now().isoformat(),
                "data": {
                    "success": False,
                    "error": f"Stream '{stream_id}' not found or expired",
                    "request_id": get_request_id() or None,
                },
            }
            yield format_sse_event(error_event, event="error")
        except asyncio.CancelledError:
            logger.info("Stream %s: observer disconnected", stream_id)
        except Exception as exc:
            logger.exception("Stream %s: unexpected error: %s", stream_id, exc)
            error_detail = str(exc) if config.DEBUG else "Internal server error"
            error_event = {
                "type": "error",
                "timestamp": utc_now().isoformat(),
                "data": {
                    "success": False,
                    "error": error_detail,
                    "request_id": get_request_id() or None,
                },
            }
            yield format_sse_event(error_event, event="error")
        finally:
            await consumer.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
