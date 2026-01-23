"""
SSE (Server-Sent Events) 工具

提供 SSE 响应格式化和事件序列化功能。
"""

import json
from typing import Dict, Any, AsyncGenerator
from datetime import datetime

from starlette.responses import StreamingResponse


def format_sse_event(
    data: Dict[str, Any],
    event: str = None,
    id: str = None,
    retry: int = None
) -> str:
    """
    格式化 SSE 事件

    SSE 格式：
        event: <event_name>
        id: <event_id>
        retry: <retry_ms>
        data: <json_data>

    Args:
        data: 事件数据（会被序列化为 JSON）
        event: 事件名称（可选）
        id: 事件 ID（可选）
        retry: 重连间隔（毫秒，可选）

    Returns:
        格式化的 SSE 字符串
    """
    lines = []

    if event:
        lines.append(f"event: {event}")

    if id:
        lines.append(f"id: {id}")

    if retry:
        lines.append(f"retry: {retry}")

    # 序列化数据，处理 datetime
    json_data = json.dumps(data, ensure_ascii=False, default=_json_serializer)
    lines.append(f"data: {json_data}")

    # SSE 事件以双换行符结尾
    return "\n".join(lines) + "\n\n"


def format_sse_comment(comment: str) -> str:
    """
    格式化 SSE 注释（用于心跳）

    SSE 注释格式：
        : <comment>

    Args:
        comment: 注释内容

    Returns:
        格式化的 SSE 注释字符串
    """
    return f": {comment}\n\n"


def _json_serializer(obj: Any) -> str:
    """
    JSON 序列化器（处理特殊类型）

    Args:
        obj: 要序列化的对象

    Returns:
        序列化后的字符串
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


async def create_sse_response(
    event_generator: AsyncGenerator[Dict[str, Any], None],
    ping_interval: int = 15
) -> StreamingResponse:
    """
    创建 SSE StreamingResponse

    Args:
        event_generator: 事件生成器
        ping_interval: 心跳间隔（秒）

    Returns:
        StreamingResponse 实例
    """
    async def generate():
        async for event in event_generator:
            yield format_sse_event(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )


class SSEResponse(StreamingResponse):
    """
    SSE 响应类

    继承自 StreamingResponse，添加 SSE 特定的 headers。
    """

    def __init__(
        self,
        content: AsyncGenerator[str, None],
        status_code: int = 200,
        headers: Dict[str, str] = None,
        **kwargs
    ):
        # 合并默认 headers
        default_headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }

        if headers:
            default_headers.update(headers)

        super().__init__(
            content=content,
            status_code=status_code,
            headers=default_headers,
            media_type="text/event-stream",
            **kwargs
        )
