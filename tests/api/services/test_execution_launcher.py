"""ExecutionLauncher thin-facade contract tests."""

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest

import api.services.execution_launcher as launcher_module
from api.services.execution_launcher import (
    ExecutionLauncher,
    ExecutionSpec,
)
from api.services.execution_runner import ConflictError
from utils.logger import reset_request_id, set_request_id


class _FakeController:
    def __init__(self) -> None:
        self.stream_kwargs = None
        self.event_stream = object()

    def stream_execute(self, **kwargs):
        self.stream_kwargs = kwargs
        return self.event_stream


class _ImmediateRunner:
    def __init__(self) -> None:
        self.submit_args = None

    async def submit(
        self,
        conversation_id,
        task_id,
        coro_factory,
        *,
        user_id,
        stream_transport,
    ):
        self.submit_args = {
            "conversation_id": conversation_id,
            "task_id": task_id,
            "user_id": user_id,
            "stream_transport": stream_transport,
        }
        await coro_factory()


class _FakeTransport:
    def __init__(self) -> None:
        self.events = []
        self.closed = []

    async def push_event(self, stream_id, event):
        self.events.append((stream_id, event))
        return True

    async def close_stream(self, stream_id):
        self.closed.append(stream_id)
        return True


async def test_submit_delegates_existing_launch_chain(monkeypatch):
    controller = _FakeController()
    controller_args = None
    pushed = None

    @asynccontextmanager
    async def fake_create_controller(conversation_id, message_id, user_id):
        nonlocal controller_args
        controller_args = (conversation_id, message_id, user_id)
        yield controller

    async def fake_run_and_push(stream_transport, stream_id, event_stream):
        nonlocal pushed
        pushed = (stream_transport, stream_id, event_stream)

    monkeypatch.setattr(launcher_module, "create_controller", fake_create_controller)
    monkeypatch.setattr(launcher_module, "run_and_push", fake_run_and_push)

    runner = _ImmediateRunner()
    transport = _FakeTransport()
    handle = await ExecutionLauncher(runner, transport).submit(ExecutionSpec(
        user_id="user-1",
        conversation_id="conv-1",
        message_id="msg-1",
        user_input="review this",
        parent_message_id="msg-parent",
        uploaded_files=[{"filename": "a.txt", "content": "a"}],
        force_compact=True,
        activate_skills=["skill-a"],
    ))

    assert controller_args == ("conv-1", "msg-1", "user-1")
    assert runner.submit_args == {
        "conversation_id": "conv-1",
        "task_id": "msg-1",
        "user_id": "user-1",
        "stream_transport": transport,
    }
    assert controller.stream_kwargs == {
        "user_input": "review this",
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "uploaded_files": [{"filename": "a.txt", "content": "a"}],
        "force_compact": True,
        "activate_skills": ["skill-a"],
        "parent_message_id": "msg-parent",
    }
    assert pushed == (transport, "msg-1", controller.event_stream)
    assert handle.conversation_id == "conv-1"
    assert handle.message_id == "msg-1"
    assert handle.stream_url == "/api/v1/stream/msg-1"


@pytest.mark.parametrize(
    ("parent_value", "expected_present"),
    [
        pytest.param(None, True, id="explicit-root"),
        pytest.param("auto", False, id="omitted-auto-parent"),
    ],
)
async def test_parent_message_tristate_is_preserved(
    monkeypatch, parent_value, expected_present
):
    controller = _FakeController()

    @asynccontextmanager
    async def fake_create_controller(*args):
        yield controller

    async def fake_run_and_push(*args):
        pass

    monkeypatch.setattr(launcher_module, "create_controller", fake_create_controller)
    monkeypatch.setattr(launcher_module, "run_and_push", fake_run_and_push)

    spec_kwargs = {}
    if parent_value != "auto":
        spec_kwargs["parent_message_id"] = parent_value
    await ExecutionLauncher(_ImmediateRunner(), _FakeTransport()).submit(ExecutionSpec(
        user_id="user-1",
        conversation_id="conv-1",
        message_id="msg-1",
        user_input="hello",
        **spec_kwargs,
    ))

    assert ("parent_message_id" in controller.stream_kwargs) is expected_present
    if expected_present:
        assert controller.stream_kwargs["parent_message_id"] is None


async def test_initialization_failure_is_sanitized_and_closes_stream(monkeypatch):
    @asynccontextmanager
    async def broken_controller(*args):
        raise RuntimeError("secret initialization detail")
        yield  # pragma: no cover - required by asynccontextmanager syntax

    monkeypatch.setattr(launcher_module, "create_controller", broken_controller)
    monkeypatch.setattr("api.services.controller_factory.config.DEBUG", False)
    ops_log = MagicMock()
    monkeypatch.setattr(launcher_module.logger, "exception", ops_log)

    transport = _FakeTransport()
    request_token = set_request_id("req-init-failure")
    try:
        await ExecutionLauncher(_ImmediateRunner(), transport).submit(ExecutionSpec(
            user_id="user-1",
            conversation_id="conv-1",
            message_id="msg-1",
            user_input="hello",
        ))
    finally:
        reset_request_id(request_token)

    assert transport.closed == ["msg-1"]
    assert len(transport.events) == 1
    stream_id, event = transport.events[0]
    assert stream_id == "msg-1"
    assert event["type"] == "error"
    assert event["data"]["error"] == "Internal server error"
    assert event["data"]["request_id"] == "req-init-failure"
    ops_log.assert_called_once()
    assert "secret initialization detail" in ops_log.call_args.args[0]


async def test_runner_conflict_propagates():
    class _ConflictingRunner:
        async def submit(self, *args, **kwargs):
            raise ConflictError("busy")

    with pytest.raises(ConflictError):
        await ExecutionLauncher(_ConflictingRunner(), _FakeTransport()).submit(ExecutionSpec(
            user_id="user-1",
            conversation_id="conv-1",
            message_id="msg-1",
            user_input="hello",
        ))
