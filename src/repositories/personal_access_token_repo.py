"""Persistence for user-owned personal access tokens."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PersonalAccessToken, User
from repositories.base import BaseRepository


class PersonalAccessTokenRepository(BaseRepository[PersonalAccessToken]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PersonalAccessToken)

    async def lock_owner(self, user_id: str) -> None:
        """Serialize the per-user active-token admission check where supported."""
        await self._session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )

    async def count_active(self, user_id: str, now: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PersonalAccessToken)
            .where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
                PersonalAccessToken.expires_at > now,
            )
        )
        return result.scalar_one()

    async def list_for_user(self, user_id: str) -> list[PersonalAccessToken]:
        result = await self._session.execute(
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == user_id)
            .order_by(PersonalAccessToken.created_at.desc())
            .limit(100)
        )
        return list(result.scalars().all())

    async def get_owned(
        self, user_id: str, token_id: str
    ) -> PersonalAccessToken | None:
        result = await self._session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.id == token_id,
                PersonalAccessToken.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def save(self, token: PersonalAccessToken) -> PersonalAccessToken:
        await self._session.flush()
        await self._session.commit()
        await self._session.refresh(token)
        return token
