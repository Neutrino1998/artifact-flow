"""
Stream Router

处理 SSE 流式输出端点：
- GET /api/v1/stream/{stream_id} - SSE 端点，订阅执行过程
"""

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from config import config
from api.dependencies import get_current_user, get_stream_transport
from api.services.auth import TokenPayload
from api.services.stream_transport import StreamNotFoundError
from api.services.stream_transport import StreamTransport
from api.utils.sse import format_sse_event, format_sse_comment
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/{stream_id}")
async def stream_events(
    stream_id: str,
    request: Request,
    current_user: TokenPayload = Depends(get_current_user),
    stream_transport: StreamTransport = Depends(get_stream_transport),
) -> StreamingResponse:
    """
    SSE 端点，订阅执行过程

    前端通过 EventSource 连接此端点，接收实时事件流。
    stream_id 即 message_id（消息与执行 1:1）。

    事件格式（使用标准 SSE event: 字段区分事件类型）：
        event: metadata
        data: {"type": "metadata", "timestamp": "...", "data": {...}}

        event: llm_chunk
        data: {"type": "llm_chunk", "timestamp": "...", "agent": "lead_agent", "data": {"content": "..."}}

        event: complete
        data: {"type": "complete", "timestamp": "...", "data": {...}}

    连接生命周期：
        - 收到 complete/error 事件后，服务端主动关闭连接
        - 前端应销毁 EventSource 实例
    """
    last_event_id = request.headers.get("Last-Event-ID")

    async def event_generator() -> AsyncGenerator[str, None]:
        """
        事件生成器

        从 StreamTransport 消费事件，格式化为 SSE 并 yield。
        """
        try:
            # 消费事件（带心跳支持 + 用户校验 + 断点续传）
            async for event in stream_transport.consume_events(
                stream_id,
                heartbeat_interval=config.SSE_PING_INTERVAL,
                user_id=current_user.user_id,
                last_event_id=last_event_id,
            ):
                # 心跳哨兵事件 → SSE 注释
                if event.get("type") == "__ping__":
                    yield format_sse_comment("ping")
                    continue

                # 提取 stream entry ID 作为 SSE id 字段
                stream_entry_id = event.pop("_stream_id", None)
                yield format_sse_event(event, event=event.get("type"), id=stream_entry_id)

                # 检查是否是终结事件
                event_type = event.get("type", "")
                if event_type in ("complete", "cancelled", "error"):
                    logger.info(f"Stream {stream_id}: terminal event '{event_type}', closing connection")
                    break

        except StreamNotFoundError:
            # stream 不存在（可能已过期）
            error_event = {
                "type": "error",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "error": f"Stream '{stream_id}' not found or expired"
                }
            }
            yield format_sse_event(error_event, event="error")

        except asyncio.CancelledError:
            # 客户端断开连接 — 不关闭 stream（producer 仍在推送，consumer 可重连）
            # 不自动 deny interrupt，依赖 PERMISSION_TIMEOUT 自然超时
            logger.info(f"Stream {stream_id}: client disconnected")

        except Exception as e:
            # 其他错误
            logger.exception(f"Stream {stream_id}: unexpected error: {e}")
            error_detail = str(e) if config.DEBUG else "Internal server error"
            error_event = {
                "type": "error",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "error": error_detail
                }
            }
            yield format_sse_event(error_event, event="error")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )
