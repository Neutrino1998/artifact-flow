"""
PR-3 — controller persist-on-external-cancel tests.

Bug ④ (2026-05-14 incident): when the outer execution task is cancelled
externally (lease fencing / shutdown), `CancelledError` is `BaseException` and
bypassed the controller's `except Exception` event-persist boundary, so the
in-memory `state["events"]` died with the task. This violated CLAUDE.md's
"events persist unconditionally" invariant — the turn left no history and was
unrecoverable on the next message.

Fix (controller.py): stream_execute's finally cancels the inner engine_task,
and run_engine's `except asyncio.CancelledError` branch calls _persist_events
directly inside engine_task (its own task, unaffected by outer cancel) before
re-raising. These tests pin that contract.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.controller import ExecutionController
from core.engine import EngineHooks
from core.events import ExecutionEvent, StreamEventType


# ============================================================
# Helpers (mirror tests/test_controller_skip_on_delete.py)
# ============================================================


def _make_mock_conversation_manager():
    cm = MagicMock()
    cm.start_conversation_async = AsyncMock(return_value="conv-test")
    cm.ensure_conversation_exists = AsyncMock()
    cm.get_active_branch = AsyncMock(return_value=None)
    cm.get_message_metadata_async = AsyncMock(return_value={})
    cm.load_event_history_async = AsyncMock(return_value=[])
    cm.add_message_async = AsyncMock()
    cm.exists_async = AsyncMock(return_value=True)
    cm.update_response_async = AsyncMock()
    cm.update_message_metadata_async = AsyncMock()
    return cm


def _make_mock_artifact_manager():
    am = MagicMock()
    am.set_session = MagicMock()
    am.flush_all = AsyncMock()
    return am


def _make_controller(conv_mgr, event_repo, art_mgr):
    hooks = EngineHooks(
        check_cancelled=AsyncMock(return_value=False),
        wait_for_interrupt=AsyncMock(return_value=None),
        drain_messages=AsyncMock(return_value=[]),
    )
    return ExecutionController(
        agents={},
        tools={},
        hooks=hooks,
        artifact_manager=art_mgr,
        conversation_manager=conv_mgr,
        message_event_repo=event_repo,
        db_manager=None,
    )


def _capturing_event_repo():
    """An event repo MagicMock that records each batch_create call."""
    batches = []
    er = MagicMock()

    async def capture_batch(events):
        batches.append(events)
        return []

    er.batch_create = capture_batch
    return er, batches


# ============================================================
# Tests
# ============================================================


class TestPersistOnExternalCancel:

    async def test_external_cancel_persists_accumulated_events(self):
        """
        Outer task cancelled while execute_loop is still running → engine_task's
        except CancelledError branch must persist the accumulated events plus a
        terminal CANCELLED event. Post-processing (exists / flush_all / update_response)
        is intentionally skipped on this path — it would never have run for a
        normally-completing externally-cancelled turn either, and engine_task
        owns the persistence contract here.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, batches = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        started = asyncio.Event()

        async def fake_execute_loop(**kwargs):
            # Accumulate an event that bug ④ would have lost
            state = kwargs["state"]
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "partial work that must survive cancel"},
                is_historical=False,
            ))
            started.set()
            # Long sleep simulates being mid-LLM-call when external cancel hits
            await asyncio.sleep(60)
            raise AssertionError("execute_loop should have been cancelled")

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), timeout=5)
            # Yield once to ensure engine_task is actually awaiting the sleep
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # Persistence happened despite external cancel
        assert len(batches) == 1, (
            f"bug ④ regression: expected 1 batch_create call, got {len(batches)}. "
            "External cancel lost events."
        )
        batch = batches[0]

        # The mid-execution LLM_COMPLETE event survived
        event_types = [e["event_type"] for e in batch]
        assert StreamEventType.LLM_COMPLETE.value in event_types, (
            f"LLM_COMPLETE missing from persisted batch: {event_types}"
        )

        # Terminal CANCELLED event was appended with the external-cancel reason
        cancelled = [e for e in batch if e["event_type"] == StreamEventType.CANCELLED.value]
        assert len(cancelled) == 1, (
            f"Expected exactly one CANCELLED terminal event, got {len(cancelled)}"
        )
        assert cancelled[0]["data"]["cancelled"] is True
        assert cancelled[0]["data"]["reason"] == "external_cancel"
        assert cancelled[0]["data"]["message_id"] == "msg-test"

        # Post-processing (exists / flush / update_response) is the cooperative-cancel
        # path; external cancel propagates out of the generator before reaching it.
        cm.exists_async.assert_not_called()
        am.flush_all.assert_not_called()
        cm.update_response_async.assert_not_called()

    async def test_external_cancel_propagates_engine_task_cancel(self):
        """
        stream_execute's finally must explicitly cancel engine_task — otherwise
        engine_task keeps running independently (this is exactly how bug ③ /
        bug ④ wedged the loop for 96 min: outer cancelled, inner ran to natural
        completion). We verify by checking execute_loop saw CancelledError.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, _ = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        cancel_observed = asyncio.Event()
        started = asyncio.Event()

        async def fake_execute_loop(**kwargs):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancel_observed.set()
                raise
            raise AssertionError("execute_loop should have been cancelled")

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        assert cancel_observed.is_set(), (
            "engine_task did not receive CancelledError — stream_execute's finally "
            "must call engine_task.cancel() so the inner task can persist events "
            "before exiting (otherwise we regress bug ③ — outer cancel can't kill "
            "a same-task synchronous wedge, but it must at least signal sibling tasks)."
        )

    async def test_persist_failure_on_cancel_does_not_shadow_cancellederror(self):
        """
        If _persist_events itself fails inside the cancel-handler, the failure must
        be logged but must NOT swallow the propagating CancelledError — the outer
        task still needs to honor the cancel request, and re-raising preserves
        normal task-cancellation semantics for the runner's cleanup path.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()

        # Repo whose batch_create blows up — the on-cancel persist attempt
        # should log and continue, not propagate this exception in place of
        # the CancelledError.
        er = MagicMock()
        er.batch_create = AsyncMock(side_effect=RuntimeError("DB exploded mid-cancel"))
        ctrl = _make_controller(cm, er, am)

        started = asyncio.Event()

        async def fake_execute_loop(**kwargs):
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "x"},
                is_historical=False,
            ))
            started.set()
            await asyncio.sleep(60)

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            # Must raise CancelledError, NOT the RuntimeError from batch_create
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # batch_create was attempted (and failed) — the swallow must be loud-logged
        # by the controller, but we just assert it was called.
        er.batch_create.assert_called_once()
