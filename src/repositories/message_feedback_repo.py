"""Message feedback persistence and admin-list queries."""

import re
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Conversation, Message, MessageFeedback


class MessageFeedbackRepository:
    """Pure data access for the one-current-feedback-per-message relation."""

    def __init__(self, session: AsyncSession):
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def upsert(
        self,
        message_id: str,
        *,
        rating: str,
        tags: list[str],
        detail: Optional[str],
    ) -> MessageFeedback:
        def apply_values(feedback: MessageFeedback) -> None:
            feedback.rating = rating
            feedback.tags = list(tags) or None
            feedback.detail = detail

        feedback = await self._session.get(MessageFeedback, message_id)
        inserting = feedback is None
        if inserting:
            feedback = MessageFeedback(message_id=message_id, rating=rating)
            apply_values(feedback)
            self._session.add(feedback)
        else:
            apply_values(feedback)

        try:
            await self._session.flush()
        except IntegrityError:
            # Two first-time PUTs can both observe no row and race to INSERT the
            # message_id PK.  Reads are the only prior work in this use-case, so a
            # full rollback is safe; recover only when the competing row now
            # exists.  An FK/check failure still has no row and is re-raised.
            await self._session.rollback()
            if not inserting:
                raise
            feedback = await self._session.get(MessageFeedback, message_id)
            if feedback is None:
                raise
            apply_values(feedback)
            await self._session.flush()

        await self._session.commit()
        await self._session.refresh(feedback)
        return feedback

    async def delete(self, message_id: str) -> bool:
        feedback = await self._session.get(MessageFeedback, message_id)
        if feedback is None:
            return False
        await self._session.delete(feedback)
        await self._session.commit()
        return True

    @staticmethod
    def _admin_filters(*, rating: Optional[str], query: Optional[str]):
        filters = []
        if rating:
            filters.append(MessageFeedback.rating == rating)
        if query:
            escaped = re.sub(r"([%_\\])", r"\\\1", query)
            filters.append(
                or_(
                    Conversation.title.ilike(f"%{escaped}%", escape="\\"),
                    Conversation.id == query,
                    Message.id == query,
                )
            )
        return filters

    async def list_admin(
        self,
        *,
        rating: Optional[str],
        query: Optional[str],
        limit: int,
        offset: int,
    ) -> list[tuple[MessageFeedback, Message, Conversation]]:
        stmt = (
            select(MessageFeedback, Message, Conversation)
            .join(Message, Message.id == MessageFeedback.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*self._admin_filters(rating=rating, query=query))
            .order_by(MessageFeedback.updated_at.desc(), MessageFeedback.message_id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows]

    async def count_admin(
        self, *, rating: Optional[str], query: Optional[str]
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(MessageFeedback)
            .join(Message, Message.id == MessageFeedback.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*self._admin_filters(rating=rating, query=query))
        )
        return (await self._session.execute(stmt)).scalar_one()
