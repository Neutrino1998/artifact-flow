"""
请求级中间件

RequestContextMiddleware:为每个 HTTP 请求铸一个 request_id,
- set 入 contextvar(供日志 / 错误事件携带,finally 中 reset);
- 在响应头注入 X-Request-ID(前端可读回,作为可回传错误码);
- 兜住所有未捕获异常 → logger.exception(完整堆栈进 error.log),
  响应未开始则返回带 request_id 的脱敏 500,已开始(SSE 流中)则 re-raise。

刻意用纯 ASGI 而非 BaseHTTPMiddleware:后者用独立 task 包裹 call_next,
contextvar 在 task 边界上传播有坑(set 在内层 task,外层 send 读不到);
纯 ASGI 全程同一 context,request_id 对日志 / 响应头 / 兜底 500 都可见。

CancelledError 是 BaseException,不会被 `except Exception` 吞 → 自然传播
(lease fencing / shutdown 的外部 cancel 安全)。
"""

from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send

from utils.logger import get_logger, set_request_id, reset_request_id

logger = get_logger("ArtifactFlow")


class RequestContextMiddleware:
    """纯 ASGI 中间件:request_id 铸造 + 注入 + 未捕获异常兜底。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # lifespan / websocket 等非 HTTP 作用域直接放行
            await self.app(scope, receive, send)
            return

        request_id = f"req-{uuid4().hex}"
        token = set_request_id(request_id)

        # 包裹 send:在 response.start 注入 X-Request-ID,并记录响应是否已开始
        response_started = False
        header_value = request_id.encode("latin-1")

        async def send_wrapper(message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", header_value))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 未捕获异常:完整堆栈进 error.log(脱敏只删 error 文本,不删定位码)
            logger.exception(f"Unhandled exception in request {request_id}")
            if response_started:
                # 响应已开始(典型是 SSE 流中途出错),无法再发 JSON,re-raise
                # 交给外层 Starlette ServerErrorMiddleware 收尾。
                raise
            await self._send_500(send, request_id)
        finally:
            reset_request_id(token)

    @staticmethod
    async def _send_500(send: Send, request_id: str) -> None:
        import json

        body = json.dumps(
            {"detail": "Internal server error", "request_id": request_id}
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"x-request-id", request_id.encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
