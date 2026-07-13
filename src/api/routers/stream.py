"""
Stream Router

处理 SSE 流式输出端点：
- GET /api/v1/stream/{stream_id} - SSE 端点，订阅执行过程
"""

import asyncio
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from config import config
from api.dependencies import get_current_user, get_stream_transport
from api.services.auth import TokenPayload
from api.services.stream_transport import StreamNotFoundError
from api.services.stream_transport import StreamTransport
from api.utils.sse import format_sse_event, format_sse_comment
from utils.time import utc_now
from utils.logger import get_logger, get_request_id

logger = get_logger("ArtifactFlow")

router = APIRouter()

# 终态事件 → SSE 连接关闭。本地副本(路由层不依赖执行语义);与
# core.events.TERMINAL_EVENT_TYPES 的一致性由 tests/core/test_terminal_event_sync.py 守护。
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
) -> StreamingResponse:
    """Build one observer's SSE response over a shared producer-owned stream.

    ``user_id`` is the stream owner identity to validate.  The regular route
    passes the authenticated user's id; the admin route first authorizes the
    conversation and then passes its stored owner id.  Observer connect/disconnect
    never changes the stream lifecycle.
    """

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

                stream_entry_id = event.pop("_stream_id", None)
                yield format_sse_event(event, event=event.get("type"), id=stream_entry_id)

                event_type = event.get("type", "")
                if event_type in _TERMINAL_EVENTS:
                    logger.info(
                        f"Stream {stream_id}: terminal event '{event_type}', closing observer"
                    )
                    break

        except StreamNotFoundError:
            logger.warning(f"Stream {stream_id}: not found or expired")
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
            logger.info(f"Stream {stream_id}: observer disconnected")

        except Exception as e:
            logger.exception(f"Stream {stream_id}: unexpected error: {e}")
            error_detail = str(e) if config.DEBUG else "Internal server error"
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


@router.get(
    "/{stream_id}",
    response_class=StreamingResponse,
    responses=SSE_OPENAPI_RESPONSES,
)
async def stream_events(
    stream_id: str,
    request: Request,
    current_user: TokenPayload = Depends(get_current_user),
    stream_transport: StreamTransport = Depends(get_stream_transport),
) -> StreamingResponse:
    """
    SSE 端点，订阅执行过程

    前端通过 fetch + ReadableStream 连接此端点，接收实时事件流。
    stream_id 即 message_id（消息与执行 1:1）。

    事件格式（使用标准 SSE event: 字段区分事件类型）：
        event: metadata
        data: {"type": "metadata", "timestamp": "...", "data": {...}}

        event: llm_chunk
        data: {"type": "llm_chunk", "timestamp": "...", "agent": "lead_agent", "data": {"content": "..."}}

        event: complete
        data: {"type": "complete", "timestamp": "...", "data": {...}}

    连接生命周期：
        - 收到 complete/cancelled/timed_out/error 事件后，服务端主动关闭连接
        - 前端应释放当前读流的 AbortController
    """
    return build_stream_response(
        stream_id=stream_id,
        stream_transport=stream_transport,
        user_id=current_user.user_id,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
