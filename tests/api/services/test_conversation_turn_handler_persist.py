"""
Handler._persist_events unit tests.

Pins the strict persistence contract:
- incomplete handler persistence wiring is rejected at construction
- returns True when there are no new events
- returns True on successful batch_create
- returns False on batch_create failure (instead of silently swallowing)
- only non-historical events (is_historical=False) are written — historical
  events were loaded from DB at turn start and must not be double-written
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from api.services.conversation_turn_handler import ConversationTurnHandler
from core.execution.agent_runtime import RuntimeHooks
from core.execution.events import ExecutionEvent, StreamEventType


def _ev(event_type, data=None, is_historical=False, agent_name="lead_agent"):
    return ExecutionEvent(
        event_type=event_type,
        agent_name=agent_name,
        data=data or {},
        is_historical=is_historical,
    )


def _make_handler(repo=None, db_manager=None):
    """Minimal handler suitable for testing _persist_events in isolation."""
    hooks = RuntimeHooks(
        check_cancelled=AsyncMock(return_value=False),
        wait_for_interrupt=AsyncMock(return_value=None),
        drain_messages=AsyncMock(return_value=[]),
    )
    kwargs = {"db_manager": db_manager} if db_manager is not None else {
        "conversation_manager": MagicMock(),
        "message_event_repo": repo or MagicMock(),
    }
    return ConversationTurnHandler(
        agents={}, tools={}, effective_toolsets={}, hooks=hooks, **kwargs
    )


class TestPersistEvents:

    def test_incomplete_persistence_wiring_is_rejected(self):
        hooks = RuntimeHooks(
            check_cancelled=AsyncMock(return_value=False),
            wait_for_interrupt=AsyncMock(return_value=None),
            drain_messages=AsyncMock(return_value=[]),
        )
        with pytest.raises(ValueError, match="requires db_manager"):
            ConversationTurnHandler(
                agents={}, tools={}, effective_toolsets={}, hooks=hooks
            )

        with pytest.raises(ValueError, match="requires db_manager"):
            ConversationTurnHandler(
                agents={}, tools={}, effective_toolsets={}, hooks=hooks,
                conversation_manager=MagicMock(),
            )

    def test_persistence_modes_are_mutually_exclusive(self):
        hooks = RuntimeHooks(
            check_cancelled=AsyncMock(return_value=False),
            wait_for_interrupt=AsyncMock(return_value=None),
            drain_messages=AsyncMock(return_value=[]),
        )
        with pytest.raises(ValueError, match="either db_manager"):
            ConversationTurnHandler(
                agents={}, tools={}, effective_toolsets={}, hooks=hooks,
                db_manager=MagicMock(),
                conversation_manager=MagicMock(),
                message_event_repo=MagicMock(),
            )

    async def test_no_new_events_returns_true(self):
        """All events historical → nothing to write → success."""
        ctrl = _make_handler(repo=MagicMock())
        state = {"events": [
            _ev(StreamEventType.USER_INPUT.value, {"content": "old"}, is_historical=True),
            _ev(StreamEventType.LLM_COMPLETE.value, {"content": "old-a"}, is_historical=True),
        ]}
        assert await ctrl._persist_events("msg-1", state) is True

    async def test_empty_events_returns_true(self):
        ctrl = _make_handler(repo=MagicMock())
        assert await ctrl._persist_events("msg-1", {"events": []}) is True

    async def test_historical_filtered_out(self):
        """Historical events (already in DB from turn-start load) must not be re-written."""
        captured = {}

        async def fake_with_retry(fn):
            # fn = lambda cm, er: er.batch_create(db_events)  (B-5 reviewer #3: 砍掉 am)
            er = MagicMock()

            async def capture_batch(events):
                captured["events"] = events
                return []

            er.batch_create = capture_batch
            return await fn(None, er)

        ctrl = _make_handler(repo=MagicMock())
        ctrl._with_db_retry = fake_with_retry  # type: ignore

        state = {"events": [
            _ev(StreamEventType.USER_INPUT.value, {"content": "old"}, is_historical=True),
            _ev(StreamEventType.USER_INPUT.value, {"content": "new1"}),
            _ev(StreamEventType.LLM_COMPLETE.value, {"content": "new2"}),
            _ev(StreamEventType.LLM_COMPLETE.value, {"content": "old-a"}, is_historical=True),
        ]}
        result = await ctrl._persist_events("msg-1", state)
        assert result is True

        written = captured["events"]
        assert len(written) == 2  # only the two non-historical
        contents = [e["data"]["content"] for e in written]
        assert "new1" in contents
        assert "new2" in contents
        assert "old" not in contents
        assert "old-a" not in contents

    async def test_failure_returns_false(self):
        """On batch_create exception, _persist_events returns False instead of swallowing."""
        async def fake_with_retry(fn):
            raise RuntimeError("DB exploded")

        ctrl = _make_handler(repo=MagicMock())
        ctrl._with_db_retry = fake_with_retry  # type: ignore

        state = {"events": [_ev(StreamEventType.USER_INPUT.value, {"content": "hi"})]}
        result = await ctrl._persist_events("msg-1", state)
        assert result is False

    async def test_integrity_error_reraises(self):
        """
        IntegrityError 透传给 caller — 让 run 区分"基础设施失败"
        和"被外部删除（FK 违规）"。后者不应被当作普通持久化失败而把整轮转 ERROR。
        """
        async def fake_with_retry(fn):
            raise IntegrityError("FK violation", None, None)

        ctrl = _make_handler(repo=MagicMock())
        ctrl._with_db_retry = fake_with_retry  # type: ignore

        state = {"events": [_ev(StreamEventType.USER_INPUT.value, {"content": "hi"})]}
        with pytest.raises(IntegrityError):
            await ctrl._persist_events("msg-1", state)

    async def test_success_assigns_event_id(self):
        """Event IDs use the {message_id}-{seq} idempotency key."""
        captured = {}

        async def fake_with_retry(fn):
            er = MagicMock()

            async def capture_batch(events):
                captured["events"] = events
                return []

            er.batch_create = capture_batch
            return await fn(None, er)

        ctrl = _make_handler(repo=MagicMock())
        ctrl._with_db_retry = fake_with_retry  # type: ignore

        state = {"events": [
            _ev(StreamEventType.USER_INPUT.value, {"content": "a"}),
            _ev(StreamEventType.LLM_COMPLETE.value, {"content": "b"}),
        ]}
        await ctrl._persist_events("msg-xyz", state)

        event_ids = [e["event_id"] for e in captured["events"]]
        assert event_ids == ["msg-xyz-0", "msg-xyz-1"]
