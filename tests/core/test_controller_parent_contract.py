"""Characterization tests for the new-turn parent tri-state contract.

The transport preserves three distinct user intents:

- omitted parent: resolve the conversation's active branch;
- explicit message ID: branch from exactly that message;
- explicit null: start a root branch and do not load prior path state.

These tests pin the controller behavior across the phase-B persistence API
split. Repository-level parent ownership and active_branch writes remain
covered by ``tests/repositories/test_conversation_repo.py``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.controller import ExecutionController
from core.engine import EngineHooks


def _conversation_manager(active_parent: str):
    manager = MagicMock()
    manager.create = AsyncMock(return_value="conv-created")
    manager.require_owned = AsyncMock()
    manager.get_active_branch = AsyncMock(return_value=active_parent)
    manager.load_event_history_async = AsyncMock(return_value=[])
    manager.get_message_metadata_async = AsyncMock(return_value={})
    manager.append_message = AsyncMock()
    # End after engine execution without exercising post-processing; that
    # lifecycle has its own exhaustive cancellation/persistence matrix.
    manager.exists_async = AsyncMock(return_value=False)
    return manager


def _controller(manager, *, user_id=None):
    hooks = EngineHooks(
        check_cancelled=AsyncMock(return_value=False),
        wait_for_interrupt=AsyncMock(return_value=None),
        drain_messages=AsyncMock(return_value=[]),
    )
    return ExecutionController(
        agents={},
        tools={},
        effective_toolsets={},
        hooks=hooks,
        conversation_manager=manager,
        message_event_repo=MagicMock(),
        user_id=user_id,
    )


async def _consume(stream):
    return [event async for event in stream]


@pytest.mark.parametrize(
    ("mode", "expected_parent", "loads_active_branch", "loads_history"),
    [
        ("auto", "msg-active", True, True),
        ("explicit", "msg-explicit", False, True),
        ("root", None, False, False),
    ],
)
async def test_parent_tristate_reaches_message_append_unchanged(
    mode,
    expected_parent,
    loads_active_branch,
    loads_history,
):
    manager = _conversation_manager(active_parent="msg-active")
    controller = _controller(manager)

    async def fake_execute_loop(**kwargs):
        state = kwargs["state"]
        state.update({
            "response": "ok",
            "error": False,
            "cancelled": False,
            "always_allowed_tools": [],
            "execution_metrics": {},
        })
        return state

    call_kwargs = {
        "user_input": "continue",
        "conversation_id": "conv-test",
        "message_id": "msg-new",
    }
    if mode == "explicit":
        call_kwargs["parent_message_id"] = "msg-explicit"
    elif mode == "root":
        call_kwargs["parent_message_id"] = None

    with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
        await _consume(controller.stream_execute(**call_kwargs))

    assert manager.get_active_branch.await_count == int(loads_active_branch)
    assert manager.load_event_history_async.await_count == int(loads_history)
    if loads_history:
        manager.load_event_history_async.assert_awaited_once_with(
            conv_id="conv-test",
            to_message_id=expected_parent,
        )
        manager.get_message_metadata_async.assert_awaited_once_with(expected_parent)
    else:
        manager.get_message_metadata_async.assert_not_awaited()

    manager.require_owned.assert_awaited_once_with("conv-test", None)
    manager.create.assert_not_awaited()
    manager.append_message.assert_awaited_once()
    append_kwargs = manager.append_message.await_args.kwargs
    assert append_kwargs["conv_id"] == "conv-test"
    assert append_kwargs["message_id"] == "msg-new"
    assert append_kwargs["parent_id"] == expected_parent


async def test_controller_allocates_id_before_explicit_create():
    manager = _conversation_manager(active_parent="unused")
    controller = _controller(manager, user_id="user-1")

    async def fake_execute_loop(**kwargs):
        state = kwargs["state"]
        state.update({
            "response": "ok",
            "error": False,
            "cancelled": False,
            "always_allowed_tools": [],
            "execution_metrics": {},
        })
        return state

    with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
        events = await _consume(controller.stream_execute(
            user_input="new conversation",
            parent_message_id=None,
            message_id="msg-new",
        ))

    created_id = manager.create.await_args.args[0]
    assert created_id.startswith("conv-")
    manager.create.assert_awaited_once_with(created_id, user_id="user-1")
    manager.require_owned.assert_not_awaited()
    assert events[0]["data"]["conversation_id"] == created_id
    assert manager.append_message.await_args.kwargs["conv_id"] == created_id
