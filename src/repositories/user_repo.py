"""
用户 Repository

提供用户的 CRUD 操作。
"""

import re
from typing import Optional, List

from sqlalchemy import select, func, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User
from repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """用户 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, User)

    async def get_by_username(self, username: str) -> Optional[User]:
        """根据用户名查询用户"""
        result = await self._session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    def _apply_search_filter(self, query, search_query: Optional[str]):
        """Apply ILIKE search on username and display_name"""
        if search_query:
            escaped = re.sub(r"([%_\\])", r"\\\1", search_query)
            pattern = f"%{escaped}%"
            query = query.where(or_(
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
            ))
        return query

    async def list_users(
        self,
        limit: int = 50,
        offset: int = 0,
        include_inactive: bool = False,
        search_query: Optional[str] = None,
    ) -> List[User]:
        """列出用户"""
        query = select(User)
        if not include_inactive:
            query = query.where(User.is_active == True)
        query = self._apply_search_filter(query, search_query)
        query = query.order_by(User.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def count_users(self, include_inactive: bool = False, search_query: Optional[str] = None) -> int:
        """统计用户总数"""
        query = select(func.count()).select_from(User)
        if not include_inactive:
            query = query.where(User.is_active == True)
        query = self._apply_search_filter(query, search_query)
        result = await self._session.execute(query)
        return result.scalar_one()

    async def hard_delete(self, user_id: str) -> bool:
        """
        硬删除用户。

        使用 Core 级 DELETE 语句而非 ORM session.delete()，确保 DB-level
        FK CASCADE 真正生效。ORM 的 session.delete() 在配合 lazy='selectin'
        加载的子集合时会主动 emit `UPDATE conversations SET user_id=NULL`，
        即使设了 passive_deletes=True 也会绕过 CASCADE — 直接走 Core 语句
        是最稳的写法。

        FK CASCADE 会连带删掉该用户的所有 conversation / messages /
        events / artifacts。如果用户有正在跑的 engine，被级联删除的
        conversation 行会被 controller post-processing 的 exists() 检查
        兜住（PR2a），不会撞 FK。

        Returns:
            True — 删除成功；False — 用户不存在
        """
        result = await self._session.execute(
            delete(User).where(User.id == user_id)
        )
        await self._session.commit()
        return result.rowcount > 0
