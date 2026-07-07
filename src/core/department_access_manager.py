"""Department-scoped access management (Phase G-1).

Rules are exception rows without an `effect` column:
- public resources: rule row means deny/exclude this department subtree.
- department resources: rule row means grant/allow this department subtree.

The manager owns that business meaning. Routers only map errors to HTTP.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Department,
    Skill,
    ToolUnit,
)
from repositories.department_access_repo import DepartmentAccessRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

_ACCESS_VISIBILITIES = {"public", "department"}


class DepartmentAccessError(Exception):
    status_code = 400


class DepartmentNotFoundError(DepartmentAccessError):
    status_code = 404


class ResourceNotFoundError(DepartmentAccessError):
    status_code = 404


class InvalidDepartmentAccessRuleError(DepartmentAccessError):
    status_code = 400


@dataclass(frozen=True)
class _RuleState:
    direct: bool
    inherited_from: Optional[Department]

    @property
    def matched(self) -> bool:
        return self.direct or self.inherited_from is not None


class DepartmentAccessManager:
    def __init__(self, session: AsyncSession):
        self._repo = DepartmentAccessRepository(session)

    async def get_department_access(self, dept_id: str) -> dict:
        chain = await self._require_department_chain(dept_id)
        dept_ids = [d.id for d in chain]
        departments_by_id = {d.id: d for d in chain}

        skill_rules = await self._repo.skill_rules_for_departments(dept_ids)
        unit_rules = await self._repo.unit_rules_for_departments(dept_ids)
        skill_rule_map = _rules_by_resource(skill_rules, "skill_slug")
        unit_rule_map = _rules_by_resource(unit_rules, "unit_name")

        skills = [
            self._serialize_skill(
                skill,
                self._rule_state(
                    skill.slug, dept_ids, departments_by_id, skill_rule_map
                ),
            )
            for skill in await self._repo.list_access_skills()
        ]
        units = [
            self._serialize_unit(
                unit,
                self._rule_state(unit.name, dept_ids, departments_by_id, unit_rule_map),
            )
            for unit in await self._repo.list_units()
            if unit.visibility in _ACCESS_VISIBILITIES
        ]

        dept = chain[0]
        return {
            "department": {
                "id": dept.id,
                "parent_id": dept.parent_id,
                "name": dept.name,
            },
            "skills": skills,
            "units": units,
        }

    async def put_skill_rule(self, dept_id: str, slug: str) -> None:
        await self._require_department(dept_id)
        skill = await self._require_skill(slug)
        self._require_skill_accessible_by_department(skill)
        await self._repo.add_skill_rule(dept_id, slug)
        await self._repo.commit()

    async def delete_skill_rule(self, dept_id: str, slug: str) -> None:
        await self._require_department(dept_id)
        skill = await self._require_skill(slug)
        self._require_skill_accessible_by_department(skill)
        await self._repo.delete_skill_rule(dept_id, slug)
        await self._repo.commit()

    async def put_unit_rule(self, dept_id: str, unit_name: str) -> None:
        await self._require_department(dept_id)
        unit = await self._require_unit(unit_name)
        self._require_unit_accessible_by_department(unit)
        await self._repo.add_unit_rule(dept_id, unit_name)
        await self._repo.commit()

    async def delete_unit_rule(self, dept_id: str, unit_name: str) -> None:
        await self._require_department(dept_id)
        unit = await self._require_unit(unit_name)
        self._require_unit_accessible_by_department(unit)
        await self._repo.delete_unit_rule(dept_id, unit_name)
        await self._repo.commit()

    async def _require_department(self, dept_id: str) -> Department:
        dept = await self._repo.get_department(dept_id)
        if dept is None:
            raise DepartmentNotFoundError(f"department '{dept_id}' does not exist")
        return dept

    async def _require_department_chain(self, dept_id: str) -> list[Department]:
        chain = await self._repo.load_ancestor_departments(dept_id)
        if not chain or chain[0].id != dept_id:
            raise DepartmentNotFoundError(f"department '{dept_id}' does not exist")
        return chain

    async def _require_skill(self, slug: str) -> Skill:
        skill = await self._repo.get_skill(slug)
        if skill is None:
            raise ResourceNotFoundError(f"skill '{slug}' does not exist")
        return skill

    async def _require_unit(self, unit_name: str) -> ToolUnit:
        unit = await self._repo.get_unit(unit_name)
        if unit is None:
            raise ResourceNotFoundError(f"unit '{unit_name}' does not exist")
        return unit

    def _require_skill_accessible_by_department(self, skill: Skill) -> None:
        if skill.visibility == "private":
            logger.warning(
                "Rejected department access rule for private skill %r", skill.slug
            )
            raise InvalidDepartmentAccessRuleError(
                f"private skill '{skill.slug}' cannot have department rules"
            )
        if skill.visibility not in _ACCESS_VISIBILITIES:
            logger.warning(
                "Rejected department access rule for skill %r with unsupported "
                "visibility %r",
                skill.slug,
                skill.visibility,
            )
            raise InvalidDepartmentAccessRuleError(
                f"skill '{skill.slug}' has unsupported visibility '{skill.visibility}'"
            )

    def _require_unit_accessible_by_department(self, unit: ToolUnit) -> None:
        if unit.visibility not in _ACCESS_VISIBILITIES:
            logger.warning(
                "Rejected department access rule for unit %r with unsupported "
                "visibility %r",
                unit.name,
                unit.visibility,
            )
            raise InvalidDepartmentAccessRuleError(
                f"unit '{unit.name}' has unsupported visibility '{unit.visibility}'"
            )

    def _rule_state(
        self,
        resource_id: str,
        dept_ids: list[str],
        departments_by_id: Mapping[str, Department],
        rule_map: Mapping[str, set[str]],
    ) -> _RuleState:
        direct = bool(dept_ids and dept_ids[0] in rule_map.get(resource_id, set()))
        inherited_from = None
        for ancestor_id in dept_ids[1:]:
            if ancestor_id in rule_map.get(resource_id, set()):
                inherited_from = departments_by_id.get(ancestor_id)
                break
        return _RuleState(direct=direct, inherited_from=inherited_from)

    def _serialize_skill(self, skill: Skill, rule: _RuleState) -> dict:
        return {
            "slug": skill.slug,
            "name": skill.name,
            "description": skill.description,
            "visibility": skill.visibility,
            "source": skill.source,
            "default_enabled": skill.default_enabled,
            "rule_action": _rule_action(skill.visibility),
            "direct_rule": rule.direct,
            "inherited_rule": _serialize_inherited_rule(rule.inherited_from),
            "effective_allowed": _effective_allowed(skill.visibility, rule.matched),
        }

    def _serialize_unit(self, unit: ToolUnit, rule: _RuleState) -> dict:
        return {
            "name": unit.name,
            "kind": unit.kind,
            "description": unit.description,
            "visibility": unit.visibility,
            "source": unit.source,
            "rule_action": _rule_action(unit.visibility),
            "direct_rule": rule.direct,
            "inherited_rule": _serialize_inherited_rule(rule.inherited_from),
            "effective_allowed": _effective_allowed(unit.visibility, rule.matched),
        }


def _rules_by_resource(rules: Iterable[object], attr: str) -> dict[str, set[str]]:
    by_resource: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        by_resource[getattr(rule, attr)].add(rule.department_id)
    return by_resource


def _rule_action(visibility: str) -> str:
    if visibility == "department":
        return "grant"
    return "deny"


def _effective_allowed(visibility: str, matched: bool) -> bool:
    if visibility == "public":
        return not matched
    if visibility == "department":
        return matched
    return False


def _serialize_inherited_rule(dept: Optional[Department]) -> Optional[dict]:
    if dept is None:
        return None
    return {"department_id": dept.id, "department_name": dept.name}
