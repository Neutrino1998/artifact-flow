"""Focused concurrency-recovery tests for MessageFeedbackRepository."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import MessageFeedback
from repositories.message_feedback_repo import MessageFeedbackRepository


def mock_session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.get = AsyncMock()
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_first_insert_conflict_reloads_and_updates_competing_row():
    session = mock_session()
    competing = MessageFeedback(
        message_id="msg-1",
        rating="positive",
        tags=["resolved_problem"],
        detail=None,
    )
    conflict = IntegrityError("duplicate message_id", None, Exception())
    session.get.side_effect = [None, competing]
    session.flush.side_effect = [conflict, None]

    result = await MessageFeedbackRepository(session).upsert(
        "msg-1",
        rating="negative",
        tags=["lost_context"],
        detail="遗漏了前文",
    )

    assert result is competing
    assert competing.rating == "negative"
    assert competing.tags == ["lost_context"]
    assert competing.detail == "遗漏了前文"
    session.rollback.assert_awaited_once()
    session.commit.assert_awaited_once()
    assert session.flush.await_count == 2


@pytest.mark.asyncio
async def test_insert_integrity_error_without_competing_row_is_reraised():
    session = mock_session()
    conflict = IntegrityError("foreign key failure", None, Exception())
    session.get.side_effect = [None, None]
    session.flush.side_effect = conflict

    with pytest.raises(IntegrityError) as raised:
        await MessageFeedbackRepository(session).upsert(
            "missing-message",
            rating="positive",
            tags=[],
            detail=None,
        )

    assert raised.value is conflict
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
