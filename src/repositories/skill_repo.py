"""Skill 数据访问(纯读,Phase C-2)。

三层职责模型的 Repository 层:只取数、不做业务/格式化,ORM 不外逃(返回标量 /
plain dict / set)。可见性解析(EffectiveSkillSet)、CRUD(C-3 Manager)在上层。
"""

from typing import Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DepartmentSkillRule, Skill, User, UserSkill


class SkillRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def user_department_id(self, user_id: str) -> Optional[str]:
        """用户当前部门(从 DB 取,不信 JWT —— dept 授权是 correctness)。"""
        return (
            await self._session.execute(
                select(User.department_id).where(User.id == user_id)
            )
        ).scalar_one_or_none()

    async def user_overrides(self, user_id: str) -> Dict[str, bool]:
        """该用户的 user_skill 稀疏覆盖 `{slug: enabled}`。"""
        rows = (
            await self._session.execute(
                select(UserSkill.skill_slug, UserSkill.enabled).where(
                    UserSkill.user_id == user_id
                )
            )
        ).all()
        return {slug: enabled for slug, enabled in rows}

    async def dept_matched_slugs(self, dept_ids: List[str]) -> Set[str]:
        """祖先链中任一部门有 department_skill_rule 例外的 skill slug 集(方向由 visibility 派生)。"""
        if not dept_ids:
            return set()
        rows = (
            await self._session.execute(
                select(DepartmentSkillRule.skill_slug).where(
                    DepartmentSkillRule.department_id.in_(dept_ids)
                )
            )
        ).scalars().all()
        return set(rows)

    async def get_skill_md(self, slug: str) -> Optional[str]:
        """L2 read_skill 的正文取数(标量,不外逃 ORM)。"""
        return (
            await self._session.execute(
                select(Skill.skill_md).where(Skill.slug == slug)
            )
        ).scalar_one_or_none()
