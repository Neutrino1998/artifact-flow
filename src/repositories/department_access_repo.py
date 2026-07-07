"""Department access repository.

Pure data access for department-scoped skill/unit rules. Business meaning of a
rule row (public=deny, department=grant) stays in DepartmentAccessManager.
"""

from dataclasses import dataclass
from typing import List, Sequence

from sqlalchemy import case, delete, exists, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Department,
    DepartmentSkillRule,
    DepartmentUnitRule,
    Skill,
    ToolUnit,
)


@dataclass(frozen=True)
class SkillAccessProjection:
    slug: str
    name: str
    description: str
    visibility: str
    source: str
    default_enabled: bool
    direct_rule: bool
    inherited_department_id: str | None


@dataclass(frozen=True)
class UnitAccessProjection:
    name: str
    kind: str
    description: str
    visibility: str
    source: str
    direct_rule: bool
    inherited_department_id: str | None


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

    async def get_skill(self, slug: str) -> Skill | None:
        return await self._session.get(Skill, slug)

    async def get_unit(self, name: str) -> ToolUnit | None:
        return await self._session.get(ToolUnit, name)

    async def get_unit_for_update(self, name: str) -> ToolUnit | None:
        return (
            await self._session.execute(
                select(ToolUnit).where(ToolUnit.name == name).with_for_update()
            )
        ).scalar_one_or_none()

    async def list_skill_access(
        self, dept_ids: Sequence[str]
    ) -> List[SkillAccessProjection]:
        """Project skill visibility and dept-rule matches in one SELECT.

        Under PostgreSQL READ COMMITTED each SELECT gets its own snapshot, so
        resource visibility and rule matches must be read by the same statement.
        """
        direct_rule, inherited_department_id = _rule_match_columns(
            DepartmentSkillRule,
            DepartmentSkillRule.skill_slug,
            Skill.slug,
            dept_ids,
        )
        rows = (
            await self._session.execute(
                select(
                    Skill.slug.label("slug"),
                    Skill.name.label("name"),
                    Skill.description.label("description"),
                    Skill.visibility.label("visibility"),
                    Skill.source.label("source"),
                    Skill.default_enabled.label("default_enabled"),
                    direct_rule,
                    inherited_department_id,
                )
                .where(Skill.visibility.in_(["public", "department"]))
                .order_by(Skill.slug)
            )
        ).mappings().all()
        return [
            SkillAccessProjection(
                slug=r["slug"],
                name=r["name"],
                description=r["description"],
                visibility=r["visibility"],
                source=r["source"],
                default_enabled=r["default_enabled"],
                direct_rule=bool(r["direct_rule"]),
                inherited_department_id=r["inherited_department_id"],
            )
            for r in rows
        ]

    async def list_unit_access(
        self, dept_ids: Sequence[str]
    ) -> List[UnitAccessProjection]:
        """Project unit visibility and dept-rule matches in one SELECT."""
        direct_rule, inherited_department_id = _rule_match_columns(
            DepartmentUnitRule,
            DepartmentUnitRule.unit_name,
            ToolUnit.name,
            dept_ids,
        )
        rows = (
            await self._session.execute(
                select(
                    ToolUnit.name.label("name"),
                    ToolUnit.kind.label("kind"),
                    ToolUnit.description.label("description"),
                    ToolUnit.visibility.label("visibility"),
                    ToolUnit.source.label("source"),
                    direct_rule,
                    inherited_department_id,
                )
                .where(ToolUnit.visibility.in_(["public", "department"]))
                .order_by(ToolUnit.name)
            )
        ).mappings().all()
        return [
            UnitAccessProjection(
                name=r["name"],
                kind=r["kind"],
                description=r["description"],
                visibility=r["visibility"],
                source=r["source"],
                direct_rule=bool(r["direct_rule"]),
                inherited_department_id=r["inherited_department_id"],
            )
            for r in rows
        ]

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

    async def has_skill_rule(self, dept_id: str, slug: str) -> bool:
        return (
            await self._session.get(DepartmentSkillRule, (dept_id, slug))
            is not None
        )

    async def has_unit_rule(self, dept_id: str, unit_name: str) -> bool:
        return (
            await self._session.get(DepartmentUnitRule, (dept_id, unit_name))
            is not None
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

    async def rollback(self) -> None:
        await self._session.rollback()


def _rule_match_columns(
    rule_cls, rule_resource_col, resource_col, dept_ids: Sequence[str]
):
    direct_id = dept_ids[0] if dept_ids else None
    direct_rule = (
        exists().where(
            rule_cls.department_id == direct_id,
            rule_resource_col == resource_col,
        )
        if direct_id
        else literal(False)
    ).label("direct_rule")

    ancestor_ids = list(dept_ids[1:])
    if ancestor_ids:
        inherited_order = case(
            {dept_id: idx for idx, dept_id in enumerate(ancestor_ids)},
            value=rule_cls.department_id,
            else_=len(ancestor_ids),
        )
        inherited_department_id = (
            select(rule_cls.department_id)
            .where(
                rule_resource_col == resource_col,
                rule_cls.department_id.in_(ancestor_ids),
            )
            .order_by(inherited_order)
            .limit(1)
            .scalar_subquery()
        )
    else:
        inherited_department_id = literal(None)

    return direct_rule, inherited_department_id.label("inherited_department_id")
