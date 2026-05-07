"""
DepartmentRepository / DB-level constraint tests.

绕开路由层 pre-check，直接 ORM 触发 INSERT，验证 DB 层约束按预期生效。
路由级 CRUD / 业务规则的覆盖在 tests/api/test_departments.py。
"""

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import Department
from repositories.department_repo import DepartmentRepository


@pytest.mark.asyncio
class TestDepartmentDBConstraints:
    """验证 schema 上的去重约束在 DB 层确实生效（不靠路由 pre-check）。"""

    async def test_duplicate_root_name_blocked_by_partial_unique_index(
        self, db_session
    ):
        """
        SQL 标准 NULL DISTINCT 语义让 UNIQUE(parent_id, name) 在 parent_id IS
        NULL 的行上失效。partial unique index `uq_dept_root_name` 兜底。

        本测两条同名根级部门直接走 ORM，第二条应被 DB 层拒绝。
        """
        db_session.add(Department(id="dept-a1", parent_id=None, name="部门A"))
        await db_session.flush()
        await db_session.commit()

        db_session.add(Department(id="dept-a2", parent_id=None, name="部门A"))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_duplicate_non_root_name_blocked_by_unique_constraint(
        self, db_session
    ):
        """非根级（parent_id 非 NULL）由 UNIQUE(parent_id, name) 约束兜。"""
        parent = Department(id="dept-parent", parent_id=None, name="父")
        db_session.add(parent)
        await db_session.flush()
        await db_session.commit()

        db_session.add(Department(id="dept-c1", parent_id=parent.id, name="子"))
        await db_session.flush()
        await db_session.commit()

        db_session.add(Department(id="dept-c2", parent_id=parent.id, name="子"))
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_same_name_different_parents_allowed(self, db_session):
        """同名 + 不同父 应允许（partial index 不应误伤）。"""
        p1 = Department(id="dept-p1", parent_id=None, name="父1")
        p2 = Department(id="dept-p2", parent_id=None, name="父2")
        db_session.add_all([p1, p2])
        await db_session.flush()
        await db_session.commit()

        # 同名但父不同 — 两条都应当能插
        db_session.add(Department(id="dept-c1", parent_id=p1.id, name="共同子"))
        db_session.add(Department(id="dept-c2", parent_id=p2.id, name="共同子"))
        await db_session.flush()
        await db_session.commit()

    async def test_root_name_distinct_from_child_name(self, db_session):
        """
        根级名 与 任意非根级行 应当各自独立 — partial index 只看 parent_id IS NULL。
        即：根 "部门A" 与 某父下的子 "部门A" 互不冲突。
        """
        parent = Department(id="dept-p", parent_id=None, name="父")
        db_session.add(parent)
        await db_session.flush()
        await db_session.commit()

        # 根级 "部门A"
        db_session.add(Department(id="dept-root-a", parent_id=None, name="部门A"))
        # 非根级也叫 "部门A"
        db_session.add(Department(id="dept-child-a", parent_id=parent.id, name="部门A"))
        await db_session.flush()
        await db_session.commit()


@pytest.mark.asyncio
class TestDepartmentRepoBasics:
    """smoke 一下 repo 的几个查询方法，主要覆盖路由不直接走的路径。"""

    async def test_count_users_and_children(
        self, department_repo: DepartmentRepository, db_session
    ):
        a = Department(id="dept-a", parent_id=None, name="A")
        b = Department(id="dept-b", parent_id="dept-a", name="B")
        c = Department(id="dept-c", parent_id="dept-a", name="C")
        db_session.add_all([a, b, c])
        await db_session.flush()
        await db_session.commit()

        assert await department_repo.count_children("dept-a") == 2
        assert await department_repo.count_children("dept-b") == 0
        # 没用户挂上 — count_users 应当 0
        assert await department_repo.count_users("dept-a") == 0

    async def test_would_create_cycle(
        self, department_repo: DepartmentRepository, db_session
    ):
        # 链：a → b → c
        a = Department(id="dept-a", parent_id=None, name="A")
        b = Department(id="dept-b", parent_id="dept-a", name="B")
        c = Department(id="dept-c", parent_id="dept-b", name="C")
        db_session.add_all([a, b, c])
        await db_session.flush()
        await db_session.commit()

        # 把 a 挂到 c 下 → 形成 a → b → c → a 的环
        assert await department_repo.would_create_cycle("dept-a", "dept-c") is True
        # 把 a 挂自己下 → 直接环
        assert await department_repo.would_create_cycle("dept-a", "dept-a") is True
        # 把 a 挂到根（None） → 不构成环
        assert await department_repo.would_create_cycle("dept-a", None) is False
        # 把 c 挂到 a 下（c 已经在 a 子树里，但 a 不在 c 祖先链上）— 不环
        assert await department_repo.would_create_cycle("dept-c", "dept-a") is False
