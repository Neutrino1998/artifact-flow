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
# Helpers (mirror tests/core/test_controller_skip_on_delete.py)
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
        artifact_service=art_mgr,
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


def _make_failing_event_repo():
    """An event repo whose batch_create raises → _persist_events returns False."""
    er = MagicMock()

    async def boom_batch(events):
        raise RuntimeError("db write failed")

    er.batch_create = boom_batch
    return er


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

        # Post-processing exists / flush are the cooperative-cancel path —
        # external cancel propagates out of the generator before reaching them.
        cm.exists_async.assert_not_called()
        am.flush_all.assert_not_called()
        # But update_response IS called from engine_task's cancel handler so
        # the frontend renders the bubble + event flow at all (MessageList
        # gates on Message.response non-empty; events list is nested inside).
        from config import config
        cm.update_response_async.assert_called_once()
        assert cm.update_response_async.call_args.kwargs["response"] == config.CANCELLED_RESPONSE_BY_SYSTEM

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

    async def test_external_cancel_during_exists_async_persists_events(self):
        """
        Reviewer-flagged gap: when the outer cancel arrives AFTER execute_loop has
        already returned (during post-processing — exists_async / flush_all / etc.),
        engine_task is already done. stream_execute's finally is a no-op, and
        run_engine's `except CancelledError` cannot fire (it already returned
        successfully). Without an outer cancel-boundary around post-processing,
        the CancelledError propagates past _persist_events and events die.

        Reproduction: execute_loop returns clean, exists_async sleeps, we cancel.
        Without the fix, batch_create stays at 0. With the fix, the outer
        try/except CancelledError late-persists once.
        """
        cm = _make_mock_conversation_manager()
        # Make exists_async block long enough for us to cancel mid-await
        async def slow_exists(*args, **kwargs):
            await asyncio.sleep(60)
            return True
        cm.exists_async = AsyncMock(side_effect=slow_exists)

        am = _make_mock_artifact_manager()
        er, batches = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        exists_called = asyncio.Event()
        original_exists = cm.exists_async

        async def signal_then_block(*args, **kwargs):
            exists_called.set()
            await asyncio.sleep(60)
            return True

        cm.exists_async = AsyncMock(side_effect=signal_then_block)

        async def fake_execute_loop(**kwargs):
            # Execute_loop completes normally — accumulates an event then returns.
            # The cancel arrives AFTER this returns, during post-processing.
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "engine ran fine; cancel hit post-processing"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "response": "ok",
                "error": False,
                "cancelled": False,
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            # Wait until post-processing is blocked inside exists_async
            await asyncio.wait_for(exists_called.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # The late-cancel handler must have run a single batch_create
        assert len(batches) == 1, (
            f"P1 regression: cancel during exists_async dropped events "
            f"(batch_create called {len(batches)} times, expected 1)"
        )
        batch = batches[0]
        event_types = [e["event_type"] for e in batch]
        # LLM_COMPLETE produced by execute_loop survived
        assert StreamEventType.LLM_COMPLETE.value in event_types
        # Late-cancel handler appended a CANCELLED terminal with the
        # post_processing-specific reason
        cancelled = [e for e in batch if e["event_type"] == StreamEventType.CANCELLED.value]
        assert len(cancelled) == 1
        assert cancelled[0]["data"]["reason"] == "external_cancel_post_processing"

        # Late-cancel handler also writes Message.response so the frontend
        # actually renders the cancelled turn (MessageList gates on response
        # non-empty; events flow is nested inside AssistantMessage).
        from config import config
        cm.update_response_async.assert_called_once()
        assert cm.update_response_async.call_args.kwargs["response"] == config.CANCELLED_RESPONSE_BY_SYSTEM

    async def test_late_cancel_keeps_existing_terminal_event(self):
        """
        If post-processing appended a COMPLETE/ERROR terminal (line 397) but cancel
        hit before _persist_events, the engine semantically DID complete — the
        cancel only affected persistence infrastructure. The late-persist handler
        must keep that terminal as-is (not overwrite with CANCELLED). Also
        protects batch_create's all-or-nothing duplicate-skip path: appending a
        new event after a partial write would break idempotency.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()

        # Make _persist_events block — we cancel after the terminal has been
        # appended (line 397) but before the persist completes.
        er = MagicMock()
        persist_started = asyncio.Event()
        persisted_batches = []

        async def slow_batch_create(events):
            persist_started.set()
            await asyncio.sleep(60)
            persisted_batches.append(events)
            return []

        er.batch_create = slow_batch_create
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "engine done"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "response": "ok",
                "error": False,
                "cancelled": False,
            }

        # Track all batches via a second patch — the late-cancel retry uses the
        # same er.batch_create, so we need to also capture its call.
        # Easier: make slow_batch_create only block on FIRST call.
        call_count = {"n": 0}

        async def conditional_slow_batch(events):
            call_count["n"] += 1
            if call_count["n"] == 1:
                persist_started.set()
                await asyncio.sleep(60)  # block first call long enough to cancel
                persisted_batches.append(events)
            else:
                # Late-cancel retry — record and return
                persisted_batches.append(events)
            return []

        er.batch_create = conditional_slow_batch

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(persist_started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # Late-cancel handler should have called batch_create a second time
        assert call_count["n"] == 2, (
            f"Expected late-cancel handler to retry batch_create, got "
            f"{call_count['n']} calls. (First call was cancelled mid-sleep, "
            f"second is the late-persist.)"
        )
        # The retry batch should contain LLM_COMPLETE + the COMPLETE terminal
        # appended by post-processing — NOT a fresh CANCELLED event (engine
        # completed successfully; the cancel only hit infrastructure).
        retry_batch = persisted_batches[-1]
        event_types = [e["event_type"] for e in retry_batch]
        assert StreamEventType.COMPLETE.value in event_types, (
            f"COMPLETE terminal should be preserved, got: {event_types}"
        )
        assert StreamEventType.CANCELLED.value not in event_types, (
            f"CANCELLED should NOT be re-appended when terminal already exists, "
            f"got: {event_types}"
        )

    async def test_late_cancel_on_engine_error_path_still_persists(self):
        """
        Reviewer P2: when execute_loop raises a normal Exception, run_engine's
        `except Exception` appends an ERROR event to initial_state["events"] but
        never assigns final_state — it stays None until the post-processing
        fallback runs. If a cancel arrives during _on_engine_exit (or any await
        before that fallback), the late-cancel handler used to see
        final_state=None and skip persistence, losing the ERROR-marked turn.

        Fix moves `final_state = initial_state` ahead of _on_engine_exit, and
        the late-cancel handler also falls back to initial_state defensively.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, batches = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        # Slow _on_engine_exit so we can cancel mid-await on the engine-error path
        on_exit_started = asyncio.Event()

        async def slow_on_exit(conv_id, msg_id):
            on_exit_started.set()
            await asyncio.sleep(60)

        ctrl._on_engine_exit = slow_on_exit

        async def fake_execute_loop(**kwargs):
            # Engine appended some work, then errors out before completion
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "partial work before engine error"},
                is_historical=False,
            ))
            raise RuntimeError("engine exploded")

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(on_exit_started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # Reviewer's repro: without the fix, batches stays at 0
        assert len(batches) == 1, (
            f"P2 regression: cancel during _on_engine_exit on engine-error path "
            f"dropped events (batch_create called {len(batches)} times, expected 1)"
        )
        batch = batches[0]
        event_types = [e["event_type"] for e in batch]
        # The pre-error work survived
        assert StreamEventType.LLM_COMPLETE.value in event_types
        # run_engine's except Exception appended an ERROR terminal — late-cancel
        # handler must preserve it (engine errored; cancel only hit infrastructure)
        error_terminals = [e for e in batch if e["event_type"] == StreamEventType.ERROR.value]
        assert len(error_terminals) == 1, (
            f"Existing ERROR terminal must be preserved; got types: {event_types}"
        )
        # And no fresh CANCELLED should have been appended on top of the ERROR
        cancelled = [e for e in batch if e["event_type"] == StreamEventType.CANCELLED.value]
        assert len(cancelled) == 0, (
            f"Should not overwrite ERROR terminal with CANCELLED, got types: {event_types}"
        )

    async def test_persist_failure_error_carries_flush_bit(self):
        """
        Reviewer P2 (round-3): the events-persist-failure ERROR is emitted at the
        transport layer (controller.py:485, bypassing decide_terminal because the
        event stream itself couldn't be written). It must STILL carry
        artifacts_flushed so the frontend doesn't mistake "missing field" for
        "uploads not persisted" and re-stage on retry → _N duplicates.

        Scenario: flush_all succeeds (artifacts land in DB), then _persist_events
        fails. The ERROR must carry artifacts_flushed=True so the composer drops
        the attachments (they're already in DB; retry won't dup them).
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()  # flush_all is an AsyncMock → succeeds
        ctrl = _make_controller(cm, _make_failing_event_repo(), am)

        async def fake_execute_loop(**kwargs):
            state = kwargs["state"]
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "done"},
                is_historical=False,
            ))
            state["completed"] = True
            state["response"] = "all done"
            return state

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            events = await asyncio.create_task(consume())

        am.flush_all.assert_called_once()  # flush ran → artifacts in DB
        errors = [e for e in events if e["type"] == StreamEventType.ERROR.value]
        assert len(errors) == 1, f"expected one transport ERROR, got: {[e['type'] for e in events]}"
        # flush succeeded + no rollback → bit True → frontend clears composer attachments
        assert errors[0]["data"]["artifacts_flushed"] is True
        # Message.response must NOT be written (events-first invariant: no history → no display)
        cm.update_response_async.assert_not_called()

    async def test_cooperative_cancel_writes_response_by_user(self):
        """
        Cooperative cancel (user clicks cancel — execute_loop's _check_cancelled
        sets state['cancelled']=True and returns normally) goes through the
        success-shaped post-processing path. Message.response must be the
        user-facing placeholder so the frontend renders the bubble (it gates
        on response being non-empty); CANCELLED_RESPONSE_BY_USER signals
        "you cancelled this" rather than "system interrupted you".
        """
        from config import config

        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, _ = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            # Mimic the engine's cooperative-cancel exit: cancelled=True,
            # completed=True, returns normally (no CancelledError raised).
            state = kwargs["state"]
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "partial"},
                is_historical=False,
            ))
            return {
                **state,
                "completed": True,
                "cancelled": True,
                "error": False,
                "response": "",  # cancelled mid-stream → no display content
            }

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            events = [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        cm.update_response_async.assert_called_once()
        assert cm.update_response_async.call_args.kwargs["response"] == config.CANCELLED_RESPONSE_BY_USER, (
            f"Cooperative cancel should write BY_USER placeholder, got: "
            f"{cm.update_response_async.call_args.kwargs['response']!r}"
        )
        # Sanity: events flow yielded the terminal CANCELLED
        kinds = [e["type"] for e in events]
        assert StreamEventType.CANCELLED.value in kinds

    async def test_response_update_race_does_not_double_write(self):
        """
        Cancel-mid-await race (reviewer P2): cancel lands during
        `await update_response_async(...)` — AFTER the DB may have committed
        but BEFORE the success path can set a "wrote it" flag. asyncio
        cancellation cannot unsend bytes that already left the machine, so
        the DB row is updated yet Python observes only CancelledError.

        Without the "claim slot BEFORE the await" pattern, the late-cancel
        handler observes response_update_attempted=False, calls
        update_response_async again with CANCELLED_RESPONSE_BY_SYSTEM, and
        clobbers the freshly-committed real response. Reviewer reproduced
        this exactly: call_args_list went
        ['real engine output', '*Task cancelled by system*'].

        We simulate the race by having the mock raise CancelledError after
        recording the call — equivalent to "DB commit happened, but our
        await was cancelled before returning". The fix sets
        response_update_attempted BEFORE the await, so late handler sees
        True and skips.
        """
        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, _ = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        update_response_calls = []

        async def racing_update_response(*args, **kwargs):
            # Record the call first (DB "commit" conceptually happens before
            # we'd return). Then raise CancelledError mid-await.
            update_response_calls.append(kwargs.get("response"))
            raise asyncio.CancelledError()

        cm.update_response_async = AsyncMock(side_effect=racing_update_response)

        async def fake_execute_loop(**kwargs):
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "real engine output"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "cancelled": False,
                "error": False,
                "response": "real engine output",
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            with pytest.raises(asyncio.CancelledError):
                await consume()

        # CRITICAL: exactly one call, with the real response. A second call
        # would mean the late handler observed response_update_attempted=False
        # and "rescued" by writing the placeholder — overwriting the real
        # response that the DB already committed.
        assert len(update_response_calls) == 1, (
            f"Response slot double-written. Calls: {update_response_calls}. "
            f"Cancel-mid-await race regression: response_update_attempted must "
            f"be set BEFORE `await update_response_async`, not after — late "
            f"handler is observing the post-await flag state."
        )
        from config import config
        assert update_response_calls[0] != config.CANCELLED_RESPONSE_BY_SYSTEM, (
            f"First (and only) call must be the success-path's real response, "
            f"got the system-cancel placeholder: {update_response_calls[0]!r}"
        )

    async def test_late_cancel_skips_response_update_when_already_committed(self):
        """
        If post-processing's update_response_async already committed (cancel
        landed at the subsequent update_metadata await), the late-cancel
        handler must NOT overwrite the real response with the CANCELLED
        placeholder — that would lose the actual engine output for a turn
        that semantically completed.
        """
        from config import config

        cm = _make_mock_conversation_manager()
        # First update_response_async succeeds; metadata blocks long enough to cancel
        metadata_started = asyncio.Event()

        async def slow_metadata(*args, **kwargs):
            metadata_started.set()
            await asyncio.sleep(60)

        cm.update_message_metadata_async = AsyncMock(side_effect=slow_metadata)

        am = _make_mock_artifact_manager()
        er, _ = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            state = kwargs["state"]
            state["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "real engine output"},
                is_historical=False,
            ))
            return {
                **state,
                "completed": True,
                "cancelled": False,
                "error": False,
                "response": "real engine output",
                "always_allowed_tools": [],
                "execution_metrics": {"total_duration_ms": 100},  # non-empty triggers metadata update
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(metadata_started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # Exactly ONE update_response_async call — the success-path one — with
        # the real engine output. Late-cancel handler must NOT have added a
        # second call with CANCELLED_RESPONSE_BY_SYSTEM (would clobber the real
        # output for a turn that genuinely completed).
        assert cm.update_response_async.call_count == 1, (
            f"Expected 1 update_response_async call (success-path only), got "
            f"{cm.update_response_async.call_count}. Late-cancel handler must "
            f"check response_updated flag before writing the placeholder."
        )
        assert cm.update_response_async.call_args.kwargs["response"] == "real engine output", (
            f"Real response was clobbered by late-cancel placeholder: "
            f"{cm.update_response_async.call_args.kwargs['response']!r}"
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

        # CRITICAL invariant: when events fail to persist, Message.response MUST
        # NOT be written either. Writing "*Task cancelled by system*" without
        # corresponding events creates a "cancel-shown-but-events-missing" state
        # — UI shows cancelled, but next turn's LLM has no history of this turn.
        # Mirrors the success-path's `if not events_persisted` ERROR-override.
        cm.update_response_async.assert_not_called()

    async def test_multiturn_late_cancel_skips_historical_terminal(self):
        """
        多轮对话:parent turn 已 COMPLETE,events 在 DB 里。turn 启动时
        controller 通过 load_event_history_async 把 path events 全标 is_historical=True
        放进 state["events"]。

        当前 turn engine 跑完后,cancel 落在 exists_async(decide_terminal 之前)。
        ensure_terminal 必须忽略 historical 段的 COMPLETE,合成本轮的 external
        CANCELLED;否则:
        - ensure_terminal adopt parent COMPLETE → 不合成 → terminal_appended=True
        - _persist_events 过滤掉 historical → 本轮 batch 只有 [LLM_COMPLETE],没有
          任何 terminal
        - 下一轮 EventHistory 重建会撞到无终态的半截 turn(沉默失败,不抛异常,DB
          内部不一致)

        Reviewer 复现:不修时 persisted batch 是 ['llm_complete']。
        """
        cm = _make_mock_conversation_manager()
        # 模拟 parent turn 的 historical events(从 DB 载入)
        parent_events = [
            ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "parent turn output"},
                is_historical=True,
            ),
            ExecutionEvent(
                event_type=StreamEventType.COMPLETE.value,
                agent_name=None,
                data={"success": True, "response": "parent turn output"},
                is_historical=True,
            ),
        ]
        cm.load_event_history_async = AsyncMock(return_value=parent_events)

        # exists_async 阻塞,让 cancel 落在 decide_terminal 之前
        exists_started = asyncio.Event()

        async def slow_exists(*args, **kwargs):
            exists_started.set()
            await asyncio.sleep(60)
            return True

        cm.exists_async = AsyncMock(side_effect=slow_exists)

        am = _make_mock_artifact_manager()
        er, batches = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            # 本轮 engine 跑完,append 一个 non-historical LLM_COMPLETE
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "current turn output"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "response": "current turn output",
                "error": False,
                "cancelled": False,
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id="parent-msg",   # 触发 load_event_history_async
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(exists_started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # late handler 调用了一次 persist
        assert len(batches) == 1, (
            f"Expected 1 batch_create call, got {len(batches)}"
        )
        batch = batches[0]
        event_types = [e["event_type"] for e in batch]

        # 本轮 LLM_COMPLETE 存在(它是 non-historical,正常入库)
        assert StreamEventType.LLM_COMPLETE.value in event_types

        # CRITICAL: 本轮 batch 必须包含 CANCELLED terminal,否则下一轮 EventHistory
        # 重建会撞到无终态的 turn。Reviewer 复现:不修时 batch == ['llm_complete']。
        cancelled = [
            e for e in batch
            if e["event_type"] == StreamEventType.CANCELLED.value
        ]
        assert len(cancelled) == 1, (
            f"本轮 batch 必须有 terminal。ensure_terminal 误 adopt 父轮 historical "
            f"COMPLETE → 跳过合成 → _persist_events 过滤 historical → 本轮 DB 里没有 "
            f"任何 terminal。实际 batch: {event_types}"
        )
        assert cancelled[0]["data"]["reason"] == "external_cancel_post_processing"

        # historical events 不应该被重复写入 DB
        for ev in batch:
            assert ev["event_id"].startswith("msg-test-"), (
                f"Persisted event should belong to current turn (msg-test-*), "
                f"got {ev['event_id']}"
            )

    async def test_late_cancel_preserves_complete_terminal_response(self):
        """
        Round-5 scenario: engine COMPLETE,decide_terminal append 了 COMPLETE,
        persist 的 `await` 中途 CancelledError 落下(DB 可能已 commit,但 Python
        没走到 `pp.events_persisted = True`)。late handler 必须:
        1. 重新跑 persist(stable IDs 幂等)
        2. 调 choose_response_for_terminal(pp) → COMPLETE → REAL response
        3. 不写 CANCELLED_RESPONSE_BY_SYSTEM

        重构前 bug:late handler 在这条路径硬编码 BY_SYSTEM,把已经 COMPLETE 的
        turn 的 Message.response 改写成 "*Task cancelled by system*",造成 UI
        矛盾(events flow 显示 COMPLETE,response bubble 显示"system cancel")。
        重构后 success path 和 late handler 都过 choose_response_for_terminal,
        terminal_type=COMPLETE 自然返回 real response,杜绝漂移。
        """
        from config import config

        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()

        # batch_create 第一次 raise CancelledError(模拟 DB commit 后 await 被打断),
        # 第二次(late handler 调用)正常返回。
        call_count = {"n": 0}

        async def racing_batch_create(events):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise asyncio.CancelledError()
            return []

        er = MagicMock()
        er.batch_create = racing_batch_create
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "real engine output"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "cancelled": False,
                "error": False,
                "response": "real engine output",
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            with pytest.raises(asyncio.CancelledError):
                await consume()

        # batch_create 被调了两次:success path(被 cancel 中断),late handler retry
        assert call_count["n"] == 2, (
            f"Expected 2 batch_create calls (success-path racing cancel + "
            f"late-handler retry), got {call_count['n']}"
        )

        # CRITICAL: update_response 写的是 REAL response,不是 BY_SYSTEM
        cm.update_response_async.assert_called_once()
        response_written = cm.update_response_async.call_args.kwargs["response"]
        assert response_written == "real engine output", (
            f"Late handler clobbered COMPLETE terminal's real response with "
            f"{response_written!r}. Should dispatch via choose_response_for_terminal "
            f"on terminal_type=COMPLETE → real response."
        )
        assert response_written != config.CANCELLED_RESPONSE_BY_SYSTEM, (
            f"Late handler wrote system-cancel placeholder over a COMPLETE turn — "
            f"events flow would show COMPLETE but bubble shows 'cancelled by system'."
        )

    async def test_late_cancel_persist_failure_does_not_write_response(self):
        """
        Twin of the previous test, but for the post-processing late-cancel path
        (controller.py late-cancel handler). When the late _persist_events call
        fails (or raises), the handler must not write the cancel placeholder to
        Message.response — same "events-in-DB invariant" as the engine_task path.
        Reviewer reproduced this gap on the engine_task path; the late-cancel
        handler had the same bug.
        """
        cm = _make_mock_conversation_manager()
        # Slow exists_async so we can land the cancel during post-processing
        exists_started = asyncio.Event()

        async def slow_exists(*args, **kwargs):
            exists_started.set()
            await asyncio.sleep(60)
            return True

        cm.exists_async = AsyncMock(side_effect=slow_exists)

        am = _make_mock_artifact_manager()
        # batch_create blows up — late-cancel persist will fail
        er = MagicMock()
        er.batch_create = AsyncMock(side_effect=RuntimeError("DB exploded mid-late-cancel"))
        ctrl = _make_controller(cm, er, am)

        async def fake_execute_loop(**kwargs):
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "engine done; cancel hits exists_async; DB then dies"},
                is_historical=False,
            ))
            return {
                **kwargs["state"],
                "completed": True,
                "cancelled": False,
                "error": False,
                "response": "",
            }

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        with patch("core.controller.execute_loop", side_effect=fake_execute_loop):
            consume_task = asyncio.create_task(consume())
            await asyncio.wait_for(exists_started.wait(), timeout=5)
            await asyncio.sleep(0)
            consume_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consume_task

        # batch_create was attempted (and failed) by the late-cancel handler
        er.batch_create.assert_called_once()
        # And update_response_async was NOT called — refusing to create the
        # cancel-shown-but-events-missing state
        cm.update_response_async.assert_not_called()


# ============================================================
# PR-B — execution timeout → TIMED_OUT terminal (single authority)
# ============================================================


class TestTimeoutTerminal:
    """超时不再停在传输层(run_and_push 的裸 SSE error),而是下沉到 engine_task 的
    asyncio.timeout → run_engine 的 except TimeoutError「带 flag 正常返回」→ 走完整
    post-processing → decide_terminal 产出唯一的 TIMED_OUT 终态。SSE 与 DB 同源。"""

    async def test_engine_timeout_produces_timed_out_terminal(self):
        """execute_loop 超过 EXECUTION_TIMEOUT → TIMED_OUT 终态落库 + Message.response
        = TIMED_OUT_RESPONSE + SSE 只产一条 timed_out(不是 error/cancelled/complete)。
        正常返回路径 → flush_all 照常跑(best-effort 保留部分 artifact)。"""
        from config import config

        cm = _make_mock_conversation_manager()
        am = _make_mock_artifact_manager()
        er, batches = _capturing_event_repo()
        ctrl = _make_controller(cm, er, am)

        started = asyncio.Event()

        async def slow_execute_loop(**kwargs):
            # 先 append 一个本轮事件(超时后应被保留),再挂起超过 deadline
            kwargs["state"]["events"].append(ExecutionEvent(
                event_type=StreamEventType.LLM_COMPLETE.value,
                agent_name="lead_agent",
                data={"content": "partial work before timeout"},
                is_historical=False,
            ))
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("execute_loop should have been timed out")

        async def consume():
            return [event async for event in ctrl.stream_execute(
                user_input="hi",
                conversation_id="conv-test",
                parent_message_id=None,
                message_id="msg-test",
            )]

        # 把 deadline 压到很小,让 asyncio.timeout 在 slow_execute_loop 挂起时触发。
        # 注意:这是内层 engine_task 的超时,consume 正常结束(无需手动 cancel)。
        with patch.object(config, "EXECUTION_TIMEOUT", 0.1):
            with patch("core.controller.execute_loop", side_effect=slow_execute_loop):
                events = await asyncio.wait_for(consume(), timeout=5)

        # events 落库一次,含本轮 LLM_COMPLETE + TIMED_OUT 终态
        assert len(batches) == 1, f"expected 1 batch_create, got {len(batches)}"
        batch = batches[0]
        event_types = [e["event_type"] for e in batch]
        assert StreamEventType.LLM_COMPLETE.value in event_types, (
            f"pre-timeout work must survive: {event_types}"
        )
        timed_out = [e for e in batch if e["event_type"] == StreamEventType.TIMED_OUT.value]
        assert len(timed_out) == 1, f"expected exactly one TIMED_OUT terminal: {event_types}"
        assert timed_out[0]["data"]["timed_out"] is True
        assert timed_out[0]["data"]["success"] is False
        # 没有被误记成 CANCELLED / COMPLETE
        assert StreamEventType.CANCELLED.value not in event_types
        assert StreamEventType.COMPLETE.value not in event_types

        # Message.response = TIMED_OUT_RESPONSE
        cm.update_response_async.assert_called_once()
        assert cm.update_response_async.call_args.kwargs["response"] == config.TIMED_OUT_RESPONSE

        # best-effort flush 照常跑(正常返回路径,非 external-cancel 的跳过 flush 路径)
        am.flush_all.assert_called_once()

        # SSE 终态:恰一条 timed_out,无 error / cancelled
        kinds = [e["type"] for e in events]
        assert kinds.count(StreamEventType.TIMED_OUT.value) == 1, f"SSE kinds: {kinds}"
        assert StreamEventType.ERROR.value not in kinds, (
            f"超时不应再产生传输层裸 error 事件(两个 authority 的旧行为): {kinds}"
        )
        assert StreamEventType.CANCELLED.value not in kinds
