"""
PR2a — handler post-processing skip-on-delete tests.

Covers the defensive scenario where the conversation row is deleted by an
admin hard-delete CASCADE or an out-of-band writer while the engine is running.
The user-facing conversation DELETE now serializes with execution through the
conversation lease, but this lower-layer guard remains necessary.
Expected behavior: post-processing detects the deletion and skips all
persistence phases instead of raising FK errors that get logged as scary
ERROR terminal events.

Layers under test:
  Layer 1 — exists_async() check at post-processing entry
  Layer 2 — IntegrityError catch around flush_all / _persist_events
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from api.services.conversation_turn_handler import ConversationTurnHandler
from core.execution.agent_runtime import RuntimeHooks
from core.execution.events import StreamEventType
from repositories.base import NotFoundError


# ============================================================
# Helpers
# ============================================================


def _make_engine_noop_state(message_id: str):
    """A minimal valid final_state for execute_loop to return."""
    return {
        "events": [],
        "response": "ok",
        "always_allowed_tools": [],
        "execution_metrics": {},
        "session_id": "conv-test",
        "message_id": message_id,
    }


def _make_mock_conversation_manager(exists_value: bool = True):
    """A conversation_manager AsyncMock pre-wired with the methods run uses."""
    cm = MagicMock()
    cm.create = AsyncMock(return_value="conv-test")
    cm.require_owned = AsyncMock()
    cm.get_active_branch = AsyncMock(return_value=None)
    cm.get_message_metadata_async = AsyncMock(return_value={})
    cm.load_event_history_async = AsyncMock(return_value=[])
    cm.append_message = AsyncMock()
    cm.exists_async = AsyncMock(return_value=exists_value)
    cm.update_response_async = AsyncMock()
    cm.update_message_metadata_async = AsyncMock()
    return cm


def _make_mock_artifact_service(flush_side_effect=None):
    am = MagicMock()
    am.set_session = MagicMock()
    if flush_side_effect is not None:
        am.flush_all = AsyncMock(side_effect=flush_side_effect)
    else:
        am.flush_all = AsyncMock()
    return am


def _make_mock_event_repo(batch_create_side_effect=None):
    er = MagicMock()
    if batch_create_side_effect is not None:
        er.batch_create = AsyncMock(side_effect=batch_create_side_effect)
    else:
        er.batch_create = AsyncMock(return_value=[])
    return er


def _make_handler(conv_mgr, event_repo, art_mgr):
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
        artifact_service=art_mgr,
        conversation_manager=conv_mgr,
        message_event_repo=event_repo,
        db_manager=None,  # use bound instances → mocks above
    )


async def _consume(stream):
    return [event async for event in stream]


# ============================================================
# Tests
# ============================================================


class TestMetadataPreview:

    async def test_exposes_only_attachment_filenames_for_live_observers(self):
        cm = _make_mock_conversation_manager(exists_value=False)
        am = _make_mock_artifact_service()
        er = _make_mock_event_repo()
        ctrl = _make_handler(cm, er, am)

        async def fake_execute_loop(**kwargs):
            return _make_engine_noop_state(kwargs["state"]["message_id"])

        with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
            events = await _consume(ctrl.run(
                user_input="review this",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
                uploaded_files=[{
                    "filename": "Brief.docx",
                    "content": "must not enter the SSE preview",
                    "content_type": "text/plain",
                    "metadata": {},
                    "blob": None,
                }],
            ))

        metadata = events[0]
        assert metadata["type"] == StreamEventType.METADATA.value
        assert metadata["data"]["user_input"] == "review this"
        assert metadata["data"]["uploaded_files"] == [{"filename": "Brief.docx"}]
        assert "content" not in metadata["data"]["uploaded_files"][0]


class TestSetupNoResurrection:

    async def test_missing_conversation_aborts_before_setup(self):
        """A queued turn must not recreate a conversation deleted before it starts."""
        cm = _make_mock_conversation_manager()
        cm.require_owned.side_effect = NotFoundError(
            "Conversation", "conv-test"
        )
        ctrl = _make_handler(cm, _make_mock_event_repo(), _make_mock_artifact_service())

        execute = AsyncMock()
        with patch("core.execution.agent_runtime.execute_loop", execute):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        assert events == []
        cm.require_owned.assert_awaited_once_with("conv-test", None)
        cm.append_message.assert_not_awaited()
        execute.assert_not_awaited()

    async def test_delete_between_setup_and_message_insert_aborts(self):
        """The second write boundary also fails closed on a concurrent DELETE."""
        cm = _make_mock_conversation_manager()
        cm.append_message.side_effect = NotFoundError(
            "Conversation", "conv-test"
        )
        ctrl = _make_handler(cm, _make_mock_event_repo(), _make_mock_artifact_service())

        execute = AsyncMock()
        with patch("core.execution.agent_runtime.execute_loop", execute):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        assert [event["type"] for event in events] == [StreamEventType.METADATA.value]
        cm.append_message.assert_awaited_once()
        execute.assert_not_awaited()


class TestPostProcessingSkipOnDelete:

    async def test_skips_when_conv_deleted_at_entry(self):
        """
        Layer 1 — exists() returns False at post-processing entry → all
        three persistence phases skipped. No terminal event yielded.
        """
        cm = _make_mock_conversation_manager(exists_value=False)
        am = _make_mock_artifact_service()
        er = _make_mock_event_repo()
        ctrl = _make_handler(cm, er, am)

        async def fake_execute_loop(**kwargs):
            return _make_engine_noop_state(kwargs["state"]["message_id"])

        with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        # Only the initial METADATA event is yielded; no terminal event
        kinds = [e["type"] for e in events]
        assert StreamEventType.METADATA.value in kinds
        assert StreamEventType.COMPLETE.value not in kinds
        assert StreamEventType.ERROR.value not in kinds

        # Persistence phases must not run
        am.flush_all.assert_not_called()
        er.batch_create.assert_not_called()
        cm.update_response_async.assert_not_called()
        cm.update_message_metadata_async.assert_not_called()

    async def test_skips_when_flush_raises_integrity_error(self):
        """
        Layer 2 — exists() returned True but conv was deleted between exists()
        and flush_all (TOCTOU). flush_all raises IntegrityError → early return,
        no event persistence, no Message.response update.
        """
        cm = _make_mock_conversation_manager(exists_value=True)
        am = _make_mock_artifact_service(
            flush_side_effect=IntegrityError("FK violation", None, None)
        )
        er = _make_mock_event_repo()
        ctrl = _make_handler(cm, er, am)

        async def fake_execute_loop(**kwargs):
            return _make_engine_noop_state(kwargs["state"]["message_id"])

        with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        kinds = [e["type"] for e in events]
        assert StreamEventType.COMPLETE.value not in kinds
        assert StreamEventType.ERROR.value not in kinds

        am.flush_all.assert_called_once()
        er.batch_create.assert_not_called()
        cm.update_response_async.assert_not_called()

    async def test_skips_when_persist_events_raises_integrity_error(self):
        """
        Layer 2 — exists() and flush_all both succeed but conv deleted before
        batch_create. _persist_events re-raises IntegrityError → caller catches,
        returns. No Message.response update.
        """
        cm = _make_mock_conversation_manager(exists_value=True)
        am = _make_mock_artifact_service()
        er = _make_mock_event_repo(
            batch_create_side_effect=IntegrityError("FK violation", None, None)
        )
        ctrl = _make_handler(cm, er, am)

        # Engine emits one new event so _persist_events actually attempts batch_create
        from core.execution.events import ExecutionEvent

        async def fake_execute_loop(**kwargs):
            state = kwargs["state"]
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "ok"},
                is_historical=False,
            ))
            return {**_make_engine_noop_state(state["message_id"]), "events": state["events"]}

        with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        # No terminal event downstream
        kinds = [e["type"] for e in events]
        assert StreamEventType.COMPLETE.value not in kinds
        assert StreamEventType.ERROR.value not in kinds

        am.flush_all.assert_called_once()
        er.batch_create.assert_called_once()
        cm.update_response_async.assert_not_called()

    async def test_normal_path_still_completes_when_conv_alive(self):
        """
        Sanity check — when exists() returns True and no IntegrityError fires,
        post-processing runs end-to-end and emits COMPLETE terminal event.
        """
        cm = _make_mock_conversation_manager(exists_value=True)
        am = _make_mock_artifact_service()
        er = _make_mock_event_repo()
        ctrl = _make_handler(cm, er, am)

        async def fake_execute_loop(**kwargs):
            return _make_engine_noop_state(kwargs["state"]["message_id"])

        with patch("core.execution.agent_runtime.execute_loop", side_effect=fake_execute_loop):
            events = await _consume(ctrl.run(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            ))

        kinds = [e["type"] for e in events]
        assert StreamEventType.COMPLETE.value in kinds

        am.flush_all.assert_called_once()
        cm.update_response_async.assert_called_once()
