"""部门祖先链解析(决策 10 的共享地基)。

给定一个 department_id,沿 `parent_id` 走到根,返回祖先链(含自身)。部门授权解析
里「父覆盖整子树」= 用户所在部门的祖先链 ∩ 规则集(命中 = 例外)。skill 侧(C)与
unit 侧(G)共用此一个 helper —— 两条 resolver 只在这里相交。
"""

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Department


async def load_ancestor_ids(
    session: AsyncSession, department_id: Optional[str]
) -> List[str]:
    """返回 [自身, 父, 祖父, ... 根] 的部门 id 列表(department_id 为 None → 空)。

    逐级查 parent_id(树深通常很浅);带 seen 集防环(parent_id 理应无环,RESTRICT +
    建树校验保证,这里只作兜底不致死循环)。
    """
    if not department_id:
        return []
    ids: List[str] = []
    seen: set = set()
    current: Optional[str] = department_id
    while current and current not in seen:
        seen.add(current)
        ids.append(current)
        current = (
            await session.execute(
                select(Department.parent_id).where(Department.id == current)
            )
        ).scalar_one_or_none()
    return ids
