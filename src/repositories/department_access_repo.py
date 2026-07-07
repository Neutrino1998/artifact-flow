"""Department access repository.

Pure data access for department-scoped skill/unit rules. Business meaning of a
rule row (public=deny, department=grant) stays in DepartmentAccessManager.
"""

from typing import List, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Department,
    DepartmentSkillRule,
    DepartmentUnitRule,
    Skill,
    ToolUnit,
)


class DepartmentAccessRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_department(self, dept_id: str) -> Department | None:
        return await self._session.get(Department, dept_id)

    async def load_ancestor_departments(self, dept_id: str) -> List[Department]:
        """Return [self, parent, ... root]; missing dept returns []."""
        chain: List[Department] = []
        seen: set[str] = set()
        current: str | None = dept_id
        while current and current not in seen:
            seen.add(current)
            dept = await self._session.get(Department, current)
            if dept is None:
                break
            chain.append(dept)
            current = dept.parent_id
        return chain

    async def list_access_skills(self) -> List[Skill]:
        rows = await self._session.execute(
            select(Skill)
            .where(Skill.visibility.in_(["public", "department"]))
            .order_by(Skill.slug)
        )
        return list(rows.scalars().all())

    async def get_skill(self, slug: str) -> Skill | None:
        return await self._session.get(Skill, slug)

    async def list_units(self) -> List[ToolUnit]:
        rows = await self._session.execute(select(ToolUnit).order_by(ToolUnit.name))
        return list(rows.scalars().all())

    async def get_unit(self, name: str) -> ToolUnit | None:
        return await self._session.get(ToolUnit, name)

    async def skill_rules_for_departments(
        self, dept_ids: Sequence[str]
    ) -> List[DepartmentSkillRule]:
        if not dept_ids:
            return []
        rows = await self._session.execute(
            select(DepartmentSkillRule).where(
                DepartmentSkillRule.department_id.in_(dept_ids)
            )
        )
        return list(rows.scalars().all())

    async def unit_rules_for_departments(
        self, dept_ids: Sequence[str]
    ) -> List[DepartmentUnitRule]:
        if not dept_ids:
            return []
        rows = await self._session.execute(
            select(DepartmentUnitRule).where(
                DepartmentUnitRule.department_id.in_(dept_ids)
            )
        )
        return list(rows.scalars().all())

    async def add_skill_rule(self, dept_id: str, slug: str) -> None:
        existing = await self._session.get(DepartmentSkillRule, (dept_id, slug))
        if existing is None:
            self._session.add(
                DepartmentSkillRule(department_id=dept_id, skill_slug=slug)
            )

    async def delete_skill_rule(self, dept_id: str, slug: str) -> None:
        await self._session.execute(
            delete(DepartmentSkillRule).where(
                DepartmentSkillRule.department_id == dept_id,
                DepartmentSkillRule.skill_slug == slug,
            )
        )

    async def add_unit_rule(self, dept_id: str, unit_name: str) -> None:
        existing = await self._session.get(DepartmentUnitRule, (dept_id, unit_name))
        if existing is None:
            self._session.add(
                DepartmentUnitRule(department_id=dept_id, unit_name=unit_name)
            )

    async def delete_unit_rule(self, dept_id: str, unit_name: str) -> None:
        await self._session.execute(
            delete(DepartmentUnitRule).where(
                DepartmentUnitRule.department_id == dept_id,
                DepartmentUnitRule.unit_name == unit_name,
            )
        )

    async def commit(self) -> None:
        await self._session.flush()
        await self._session.commit()
