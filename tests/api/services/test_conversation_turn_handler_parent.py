"""An admitted handler receives an already-resolved parent from Admission."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.conversation_turn_handler import ConversationTurnHandler
from core.execution.agent_runtime import RuntimeHooks


def _conversation_manager(active_parent: str):
    manager = MagicMock()
    manager.require_owned = AsyncMock()
    manager.get_active_branch = AsyncMock(return_value=active_parent)
    manager.load_event_history_async = AsyncMock(return_value=[])
    manager.get_message_metadata_async = AsyncMock(return_value={})
    manager.append_message = AsyncMock()
    # End after engine execution without exercising post-processing; that
    # lifecycle has its own exhaustive cancellation/persistence matrix.
    manager.exists_async = AsyncMock(return_value=False)
    return manager


def _handler(manager, *, user_id=None):
    hooks = RuntimeHooks(
        check_cancelled=AsyncMock(return_value=False),
        wait_for_interrupt=AsyncMock(return_value=None),
        drain_messages=AsyncMock(return_value=[]),
    )
    return ConversationTurnHandler(
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
    ("expected_parent", "loads_history"),
    [
        ("msg-explicit", True),
        (None, False),
    ],
)
async def test_resolved_parent_reaches_message_append_unchanged(
    expected_parent,
    loads_history,
):
    manager = _conversation_manager(active_parent="msg-active")
    handler = _handler(manager)

    async def fake_execute_loop(**kwargs):
        state = kwargs["state"]
        state.update({
            "response": "ok",
            "always_allowed_tools": [],
            "execution_metrics": {},
        })
        return state

    call_kwargs = {
        "user_input": "continue",
        "conversation_id": "conv-test",
        "message_id": "msg-new",
        "parent_message_id": expected_parent,
    }

    with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
        await _consume(handler.run(**call_kwargs))

    manager.get_active_branch.assert_not_awaited()
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
    manager.append_message.assert_awaited_once()
    append_kwargs = manager.append_message.await_args.kwargs
    assert append_kwargs["conv_id"] == "conv-test"
    assert append_kwargs["message_id"] == "msg-new"
    assert append_kwargs["parent_id"] == expected_parent
