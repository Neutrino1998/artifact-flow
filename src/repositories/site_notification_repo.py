"""全站通知单行配置的数据访问。"""

from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SiteNotificationConfig


class SiteNotificationRepository:
    CONFIG_ID = 1

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self) -> Optional[SiteNotificationConfig]:
        return (
            await self._session.execute(
                select(SiteNotificationConfig).where(
                    SiteNotificationConfig.id == self.CONFIG_ID
                )
            )
        ).scalar_one_or_none()

    async def compare_and_swap(
        self,
        notifications: list[dict],
        *,
        expected_revision: int,
    ) -> Optional[SiteNotificationConfig]:
        """按 revision 原子替换整组通知；冲突返回 None。

        Alembic 会预置 singleton。SQLite create_all 不跑 migration，因此保留
        expected_revision=0 时的首次 INSERT 分支；并发 INSERT 的唯一键冲突同样
        映射成 CAS 失败。
        """
        result = await self._session.execute(
            update(SiteNotificationConfig)
            .where(
                SiteNotificationConfig.id == self.CONFIG_ID,
                SiteNotificationConfig.revision == expected_revision,
            )
            .values(
                notifications=notifications,
                revision=SiteNotificationConfig.revision + 1,
                updated_at=func.now(),
            )
        )
        if result.rowcount == 1:
            await self._session.commit()
            return await self.get()

        await self._session.rollback()
        if expected_revision != 0:
            return None

        row = SiteNotificationConfig(
            id=self.CONFIG_ID,
            notifications=notifications,
            revision=1,
        )
        self._session.add(row)
        try:
            await self._session.flush()
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            return None
        return await self.get()
