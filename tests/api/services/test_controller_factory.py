"""
Tests for controller_factory event sanitization and stream forwarding.

契约:
- 注入 request_id(取自 contextvar)—— 不论 DEBUG 都注入,是可回传定位码。
- 脱敏只动 error 字段:prod 替换成 "Internal server error",DEBUG 保留原文。
- 非 error 事件原样返回。
"""

import pytest

from api.services.controller_factory import run_and_push, sanitize_error_event
from utils.logger import set_request_id, reset_request_id


@pytest.fixture
def with_request_id():
    token = set_request_id("req-cafebabe1234")
    yield "req-cafebabe1234"
    reset_request_id(token)


def _error_event() -> dict:
    return {"type": "error", "data": {"success": False, "error": "secret stack trace"}}


def test_prod_strips_error_but_keeps_request_id(monkeypatch, with_request_id):
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    out = sanitize_error_event(_error_event())
    assert out["data"]["error"] == "Internal server error"  # 脱敏
    assert out["data"]["request_id"] == with_request_id      # 定位码保留


def test_debug_keeps_error_and_injects_request_id(monkeypatch, with_request_id):
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", True)
    out = sanitize_error_event(_error_event())
    assert out["data"]["error"] == "secret stack trace"      # DEBUG 保留原文
    assert out["data"]["request_id"] == with_request_id


def test_no_request_id_when_context_empty(monkeypatch):
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    out = sanitize_error_event(_error_event())
    # 无 contextvar 时不注入空码(避免污染)
    assert "request_id" not in out["data"]


def test_existing_request_id_not_overwritten(monkeypatch, with_request_id):
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    ev = {"type": "error", "data": {"success": False, "error": "x", "request_id": "req-preset"}}
    out = sanitize_error_event(ev)
    assert out["data"]["request_id"] == "req-preset"


def test_non_error_event_passthrough(monkeypatch, with_request_id):
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    ev = {"type": "llm_complete", "data": {"content": "hello"}}
    out = sanitize_error_event(ev)
    assert out == ev  # 非 error 事件不动


async def test_run_and_push_coalesces_tool_progress_in_its_own_channel():
    class RecordingTransport:
        def __init__(self):
            self.events = []

        async def push_event(self, _stream_id, event):
            self.events.append(event)
            return True

        async def close_stream(self, _stream_id):
            return True

    async def event_stream():
        # The first chunk flushes immediately.  The remaining content/progress
        # snapshots are buffered together; sharing a channel would overwrite
        # one of them before COMPLETE forces the flush.
        yield {"type": "llm_chunk", "data": {"content": "first"}}
        yield {"type": "llm_chunk", "data": {"tool_call_progress": [{
            "index": 0, "name": "write", "arguments_chars": 10,
        }]}}
        yield {"type": "llm_chunk", "data": {"content": "latest"}}
        yield {"type": "llm_chunk", "data": {"reasoning_content": "thinking"}}
        yield {"type": "complete", "data": {"response": "done"}}

    transport = RecordingTransport()
    await run_and_push(transport, "msg-1", event_stream())

    llm_data = [event["data"] for event in transport.events if event["type"] == "llm_chunk"]
    assert any("tool_call_progress" in data for data in llm_data)
    assert any(data.get("content") == "latest" for data in llm_data)
    assert any(data.get("reasoning_content") == "thinking" for data in llm_data)
