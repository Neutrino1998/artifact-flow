"""Department-management use cases."""

from __future__ import annotations

import uuid
from typing import Optional

from db.models import Department
from repositories.base import DuplicateError
from repositories.department_repo import DepartmentRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DepartmentManagerError(Exception):
    """Base error for department use cases."""


class DepartmentNotFoundError(DepartmentManagerError):
    pass


class DepartmentInvalidParentError(DepartmentManagerError):
    pass


class DepartmentCycleError(DepartmentManagerError):
    pass


class DepartmentConflictError(DepartmentManagerError):
    pass


class DepartmentNotEmptyError(DepartmentManagerError):
    def __init__(self, *, user_count: int, child_count: int):
        self.user_count = user_count
        self.child_count = child_count
        super().__init__("Department is not empty")


class DepartmentManager:
    def __init__(self, repository: DepartmentRepository):
        self._repository = repository

    @staticmethod
    def _new_id() -> str:
        return f"dept-{uuid.uuid4()}"

    async def _serialize(self, department: Department) -> dict:
        return {
            "id": department.id,
            "parent_id": department.parent_id,
            "name": department.name,
            "user_count": await self._repository.count_users(department.id),
            "child_count": await self._repository.count_children(department.id),
            "created_at": department.created_at,
            "updated_at": department.updated_at,
        }

    async def list_children(self, parent_id: Optional[str]) -> list[dict]:
        children = await self._repository.list_children(parent_id)
        return [await self._serialize(child) for child in children]

    async def get_tree(self) -> list[dict]:
        departments = await self._repository.list_all()
        user_counts = await self._repository.user_counts_by_department()
        children_by_parent: dict[Optional[str], list[Department]] = {}
        for department in departments:
            children_by_parent.setdefault(department.parent_id, []).append(department)

        def build_node(department: Department) -> dict:
            return {
                "id": department.id,
                "parent_id": department.parent_id,
                "name": department.name,
                "user_count": user_counts.get(department.id, 0),
                "children": [
                    build_node(child)
                    for child in children_by_parent.get(department.id, [])
                ],
            }

        return [build_node(department) for department in children_by_parent.get(None, [])]

    async def get(self, department_id: str) -> dict:
        department = await self._repository.get_by_id(department_id)
        if department is None:
            raise DepartmentNotFoundError("Department not found")
        return await self._serialize(department)

    async def create(self, *, name: str, parent_id: Optional[str]) -> dict:
        if parent_id is not None and await self._repository.get_by_id(parent_id) is None:
            raise DepartmentInvalidParentError(
                "parent_id does not reference an existing department"
            )
        if await self._repository.find_by_parent_and_name(parent_id, name) is not None:
            raise DepartmentConflictError(
                f"A department named '{name}' already exists under this parent"
            )
        try:
            department = await self._repository.create_department(
                department_id=self._new_id(), parent_id=parent_id, name=name
            )
        except DuplicateError as exc:
            raise DepartmentConflictError(
                f"A department named '{name}' already exists under this parent"
            ) from exc
        logger.info(
            "Department created: %s (id=%s, parent=%s)",
            department.name,
            department.id,
            department.parent_id,
        )
        return await self._serialize(department)

    async def rename(self, department_id: str, *, name: str) -> dict:
        department = await self._repository.get_by_id(department_id)
        if department is None:
            raise DepartmentNotFoundError("Department not found")
        if department.name == name:
            return await self._serialize(department)
        conflict = await self._repository.find_by_parent_and_name(
            department.parent_id, name
        )
        if conflict is not None and conflict.id != department.id:
            raise DepartmentConflictError(
                f"A department named '{name}' already exists under the same parent"
            )
        department.name = name
        try:
            await self._repository.update_department(department)
        except DuplicateError as exc:
            raise DepartmentConflictError(
                f"A department named '{name}' already exists under the same parent"
            ) from exc
        logger.info("Department renamed: %s → %r", department.id, department.name)
        return await self._serialize(department)

    async def move(
        self, department_id: str, *, new_parent_id: Optional[str]
    ) -> dict:
        department = await self._repository.get_by_id(department_id)
        if department is None:
            raise DepartmentNotFoundError("Department not found")
        if (
            new_parent_id is not None
            and await self._repository.get_by_id(new_parent_id) is None
        ):
            raise DepartmentInvalidParentError(
                "new_parent_id does not reference an existing department"
            )
        if await self._repository.would_create_cycle(department_id, new_parent_id):
            raise DepartmentCycleError(
                "Cannot move department under itself or its descendants"
            )
        if department.parent_id == new_parent_id:
            return await self._serialize(department)
        conflict = await self._repository.find_by_parent_and_name(
            new_parent_id, department.name
        )
        if conflict is not None and conflict.id != department.id:
            raise DepartmentConflictError(
                f"A department named '{department.name}' already exists under the new parent"
            )
        department.parent_id = new_parent_id
        try:
            await self._repository.update_department(department)
        except DuplicateError as exc:
            raise DepartmentConflictError(
                f"A department named '{department.name}' already exists under the new parent"
            ) from exc
        logger.info("Department moved: %s → parent=%s", department.id, department.parent_id)
        return await self._serialize(department)

    async def delete(self, department_id: str) -> None:
        department = await self._repository.get_by_id(department_id)
        if department is None:
            raise DepartmentNotFoundError("Department not found")
        user_count = await self._repository.count_users(department_id)
        child_count = await self._repository.count_children(department_id)
        if user_count or child_count:
            raise DepartmentNotEmptyError(
                user_count=user_count, child_count=child_count
            )
        name = department.name
        await self._repository.delete(department)
        logger.info("Department deleted: %s (%r)", department_id, name)

    async def resolve_path(self, path: list[str]) -> Optional[str]:
        cleaned = [segment.strip() for segment in path if segment and segment.strip()]
        if not cleaned:
            return None
        parent_id: Optional[str] = None
        for name in cleaned:
            existing = await self._repository.find_by_parent_and_name(parent_id, name)
            if existing is not None:
                parent_id = existing.id
                continue
            try:
                created = await self._repository.create_department(
                    department_id=self._new_id(), parent_id=parent_id, name=name
                )
                parent_id = created.id
            except DuplicateError:
                existing = await self._repository.find_by_parent_and_name(parent_id, name)
                if existing is None:
                    raise
                parent_id = existing.id
        return parent_id
