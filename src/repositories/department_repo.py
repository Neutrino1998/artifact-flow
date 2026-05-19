"""
Department Repository

提供部门的 CRUD + 树查询。

部门表预计长期只有几十到几百行，所有 list/tree 查询都允许全量拉取，
不上分页（前端复用同一份数据建反向索引）。
"""

from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Department, User
from repositories.base import BaseRepository


class DepartmentRepository(BaseRepository[Department]):
    """部门 Repository"""

    def __init__(self, session: AsyncSession):
        super().__init__(session, Department)

    async def list_children(self, parent_id: Optional[str]) -> List[Department]:
        """列出某父下的直接子部门（parent_id=None 列一级部门）"""
        query = (
            select(Department)
            .where(Department.parent_id == parent_id)
            .order_by(Department.name)
        )
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def list_all(self) -> List[Department]:
        """全量拉取所有部门 — tree 渲染、子树展开、cascader 反查父链都用这一份"""
        result = await self._session.execute(
            select(Department).order_by(Department.parent_id, Department.name)
        )
        return list(result.scalars().all())

    async def find_by_parent_and_name(
        self, parent_id: Optional[str], name: str
    ) -> Optional[Department]:
        """按 (parent_id, name) 精确匹配 — resolve_department_path 用"""
        query = select(Department).where(
            Department.parent_id == parent_id,
            Department.name == name,
        )
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def count_users(self, dept_id: str) -> int:
        """统计部门下直属用户数（不含子部门）"""
        result = await self._session.execute(
            select(func.count())
            .select_from(User)
            .where(User.department_id == dept_id)
        )
        return result.scalar_one()

    async def count_children(self, dept_id: str) -> int:
        """统计直接子部门数"""
        result = await self._session.execute(
            select(func.count())
            .select_from(Department)
            .where(Department.parent_id == dept_id)
        )
        return result.scalar_one()

    async def get_ancestor_chain(self, dept_id: str) -> List[Department]:
        """
        从 dept_id 沿 parent_id 链一路向上，返回 root → leaf 顺序的 Department 列表。

        dept_id 找不到 → 返回 []。链路超过 100 层（数据异常）→ 截断返回当前累积。
        给 /auth/me 拼 department_path 用，单次 /me 至多 ~10 次 SELECT，可接受。
        """
        chain: List[Department] = []
        cursor: Optional[str] = dept_id
        for _ in range(100):
            if cursor is None:
                break
            result = await self._session.execute(
                select(Department).where(Department.id == cursor)
            )
            node = result.scalar_one_or_none()
            if node is None:
                break
            chain.append(node)
            cursor = node.parent_id
        chain.reverse()
        return chain

    async def would_create_cycle(
        self, dept_id: str, new_parent_id: Optional[str]
    ) -> bool:
        """
        判断把 dept_id 的 parent 改成 new_parent_id 是否构成环。

        从 new_parent_id 沿 parent_id 链向上遍历到根，途中遇到 dept_id 即环。
        new_parent_id=None（搬到根）永远安全；new_parent_id=dept_id（自己）算环。
        """
        if new_parent_id is None:
            return False
        if new_parent_id == dept_id:
            return True

        cursor: Optional[str] = new_parent_id
        # 加深度上限防数据库脏数据导致死循环（部门表理论上不会超过这个深度）
        for _ in range(100):
            if cursor is None:
                return False
            if cursor == dept_id:
                return True
            row = await self._session.execute(
                select(Department.parent_id).where(Department.id == cursor)
            )
            cursor = row.scalar_one_or_none()
        # 走到这里说明链超过 100 层 — 数据已经异常，按环处理拒绝
        return True
