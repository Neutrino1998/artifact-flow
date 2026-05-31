"""
Tests for RequestContextMiddleware (request-id 基础设施).

两层覆盖：
- 真 app(create_app + anon_client):正常请求响应头带 X-Request-ID,
  验证中间件确实注册进生产中间件栈。
- 最小 app(只挂中间件 + 一个抛异常的路由):未捕获异常 → 带 request_id 的
  脱敏 500,且内部细节不泄漏;contextvar 在 handler 内可见且与响应头一致。
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.middleware import RequestContextMiddleware
from utils.logger import get_request_id


# ---------- 真 app:正常请求带 X-Request-ID ----------


async def test_normal_request_has_request_id_header(anon_client):
    """生产 app 的任意请求(含未鉴权 health)响应头都带 req-xxxx。"""
    res = await anon_client.get("/health/live")
    assert res.status_code == 200
    rid = res.headers.get("X-Request-ID")
    assert rid is not None and rid.startswith("req-"), res.headers


# ---------- 最小 app:异常 / contextvar 行为 ----------


def _minimal_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ok")
    async def ok():
        # handler 内读到的 request_id 应与响应头一致
        return {"rid": get_request_id()}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("internal secret detail")

    return app


@pytest.fixture
async def mini_client():
    transport = ASGITransport(app=_minimal_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_request_id_visible_in_handler_matches_header(mini_client):
    res = await mini_client.get("/ok")
    assert res.status_code == 200
    rid = res.headers.get("X-Request-ID")
    assert rid and rid.startswith("req-")
    assert res.json()["rid"] == rid  # contextvar 在 handler 内可见且一致


async def test_unhandled_exception_returns_sanitized_500_with_request_id(mini_client):
    res = await mini_client.get("/boom")
    assert res.status_code == 500
    body = res.json()
    # 脱敏:只给通用文案,不泄漏内部细节
    assert body["detail"] == "Internal server error"
    assert "internal secret detail" not in res.text
    # 定位码:body 与响应头都带,且一致
    assert body["request_id"].startswith("req-")
    assert body["request_id"] == res.headers.get("X-Request-ID")


async def test_request_ids_are_unique_per_request(mini_client):
    r1 = await mini_client.get("/ok")
    r2 = await mini_client.get("/ok")
    assert r1.headers["X-Request-ID"] != r2.headers["X-Request-ID"]
