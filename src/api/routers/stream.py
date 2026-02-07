"""
Stream Router

处理 SSE 流式输出端点：
- GET /api/v1/stream/{thread_id} - SSE 端点，订阅执行过程
"""

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse

from api.config import config
from api.dependencies import get_stream_manager
from api.services.stream_manager import StreamManager, StreamNotFoundError
from api.utils.sse import format_sse_event, format_sse_comment
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/{thread_id}")
async def stream_events(
    thread_id: str,
    stream_manager: StreamManager = Depends(get_stream_manager),
) -> StreamingResponse:
    """
    SSE 端点，订阅 Graph 执行过程

    前端通过 EventSource 连接此端点，接收实时事件流。

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
    async def event_generator() -> AsyncGenerator[str, None]:
        """
        事件生成器

        从 StreamManager 消费事件，格式化为 SSE 并 yield。
        """
        try:
            # 消费事件（带心跳支持）
            async for event in stream_manager.consume_events(
                thread_id, heartbeat_interval=config.SSE_PING_INTERVAL
            ):
                # 心跳哨兵事件 → SSE 注释
                if event.get("type") == "__ping__":
                    yield format_sse_comment("ping")
                    continue

                yield format_sse_event(event, event=event.get("type"))

                # 检查是否是终结事件
                event_type = event.get("type", "")
                if event_type in ("complete", "error"):
                    logger.info(f"Stream {thread_id}: terminal event '{event_type}', closing connection")
                    break

        except StreamNotFoundError:
            # stream 不存在（可能已过期）
            error_event = {
                "type": "error",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "error": f"Stream '{thread_id}' not found or expired"
                }
            }
            yield format_sse_event(error_event, event="error")

        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info(f"Stream {thread_id}: client disconnected")
            await stream_manager.close_stream(thread_id)

        except Exception as e:
            # 其他错误
            logger.exception(f"Stream {thread_id}: unexpected error: {e}")
            error_event = {
                "type": "error",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
                "data": {
                    "success": False,
                    "error": str(e)
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
