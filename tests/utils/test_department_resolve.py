"""Tests for utils.department_resolve.resolve_department_path."""

import pytest
from sqlalchemy import select

from db.models import Department
from repositories.department_repo import DepartmentRepository
from utils.department_resolve import resolve_department_path


@pytest.mark.asyncio
class TestResolveDepartmentPath:
    async def test_empty_path_returns_none(self, department_repo: DepartmentRepository):
        result = await resolve_department_path(department_repo, [])
        assert result is None

    async def test_blank_path_returns_none(self, department_repo: DepartmentRepository):
        result = await resolve_department_path(department_repo, ["", "  ", "\t"])
        assert result is None

    async def test_single_level_creates(self, department_repo: DepartmentRepository):
        result = await resolve_department_path(department_repo, ["部门A"])
        assert result is not None
        dept = await department_repo.get_by_id(result)
        assert dept is not None
        assert dept.name == "部门A"
        assert dept.parent_id is None

    async def test_multi_level_creates_chain(
        self, department_repo: DepartmentRepository, db_session
    ):
        leaf_id = await resolve_department_path(
            department_repo, ["部门A", "子部门A1", "小组A1a"]
        )
        # Verify all three levels created
        all_depts = (await db_session.execute(select(Department))).scalars().all()
        names = sorted(d.name for d in all_depts)
        assert names == ["子部门A1", "小组A1a", "部门A"]

        # Verify chain structure
        leaf = await department_repo.get_by_id(leaf_id)
        assert leaf.name == "小组A1a"
        mid = await department_repo.get_by_id(leaf.parent_id)
        assert mid.name == "子部门A1"
        root = await department_repo.get_by_id(mid.parent_id)
        assert root.name == "部门A"
        assert root.parent_id is None

    async def test_reuse_existing(self, department_repo: DepartmentRepository):
        first = await resolve_department_path(department_repo, ["部门A", "子部门A1"])
        second = await resolve_department_path(department_repo, ["部门A", "子部门A1"])
        assert first == second

    async def test_partial_reuse_extends_chain(
        self, department_repo: DepartmentRepository, db_session
    ):
        # Pre-create top two levels
        await resolve_department_path(department_repo, ["部门A", "子部门A1"])
        # Now extend with a third
        leaf = await resolve_department_path(
            department_repo, ["部门A", "子部门A1", "小组A1a"]
        )
        assert leaf is not None
        # Total dept count = 3 (no duplicate root/mid)
        count = (await db_session.execute(
            select(Department)
        )).scalars().all()
        assert len(count) == 3

    async def test_strips_whitespace(self, department_repo: DepartmentRepository):
        result = await resolve_department_path(
            department_repo, ["  部门A  ", "\t子部门A1\t"]
        )
        assert result is not None
        leaf = await department_repo.get_by_id(result)
        assert leaf.name == "子部门A1"  # stripped
        root = await department_repo.get_by_id(leaf.parent_id)
        assert root.name == "部门A"

    async def test_skips_blank_segments(self, department_repo: DepartmentRepository):
        # Blank segments are skipped, not preserved as empty levels
        result = await resolve_department_path(
            department_repo, ["部门A", "", "  ", "子部门A1"]
        )
        assert result is not None
        leaf = await department_repo.get_by_id(result)
        # 子部门A1 directly under 部门A (blank segments stripped)
        assert leaf.name == "子部门A1"
        parent = await department_repo.get_by_id(leaf.parent_id)
        assert parent.name == "部门A"
