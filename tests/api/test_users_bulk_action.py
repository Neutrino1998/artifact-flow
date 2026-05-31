"""
PR5a — Bulk-action / bulk-impact endpoint integration tests.

Covers:
- POST /api/v1/admin/users/bulk-action (disable / enable / delete / set_department)
- GET /api/v1/admin/users/bulk-impact

Auth + each action's happy path + self-protection + not-found + set_department
validation + capacity + impact aggregation. Engine fail-soft on active execution
is covered by tests/core/test_controller_skip_on_delete.py (PR2a layer).
"""

import uuid
from typing import List

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db.database import DatabaseManager
from db.models import Conversation, Department, User
from repositories.conversation_repo import ConversationRepository
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository
from api.services.auth import hash_password


# ============================================================
# Helpers
# ============================================================


async def _seed_user(
    db_manager: DatabaseManager,
    *,
    role: str = "user",
    is_active: bool = True,
    department_id: str | None = None,
) -> User:
    user = User(
        id=f"user-{uuid.uuid4().hex}",
        username=f"u-{uuid.uuid4().hex[:8]}",
        hashed_password=hash_password("pw1234"),
        role=role,
        is_active=is_active,
        department_id=department_id,
    )
    async with db_manager.session() as s:
        repo = UserRepository(s)
        return await repo.add(user)


async def _seed_department(
    db_manager: DatabaseManager, name: str = "部门A"
) -> Department:
    dept = Department(
        id=f"dept-{uuid.uuid4().hex}",
        parent_id=None,
        name=name,
    )
    async with db_manager.session() as s:
        repo = DepartmentRepository(s)
        return await repo.add(dept)


async def _seed_conv(db_manager: DatabaseManager, user_id: str) -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    async with db_manager.session() as s:
        repo = ConversationRepository(s)
        await repo.create_conversation(
            conversation_id=conv_id, title="t", user_id=user_id,
        )
    return conv_id


async def _get_user(db_manager: DatabaseManager, user_id: str) -> User | None:
    async with db_manager.session() as s:
        result = await s.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()


# ============================================================
# Auth
# ============================================================


class TestAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": ["u-x"], "action": "disable"},
        )
        assert resp.status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": ["u-x"], "action": "disable"},
        )
        assert resp.status_code == 403

    async def test_impact_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/admin/users/bulk-impact?ids=u-x")
        assert resp.status_code == 401

    async def test_impact_regular_user_blocked(self, client: AsyncClient):
        resp = await client.get("/api/v1/admin/users/bulk-impact?ids=u-x")
        assert resp.status_code == 403


# ============================================================
# Disable / Enable
# ============================================================


class TestDisableEnable:
    async def test_disable_active_users(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u1 = await _seed_user(db_manager, is_active=True)
        u2 = await _seed_user(db_manager, is_active=True)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [u1.id, u2.id], "action": "disable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["succeeded"]) == sorted([u1.id, u2.id])
        assert body["failed"] == []

        for uid in (u1.id, u2.id):
            row = await _get_user(db_manager, uid)
            assert row is not None and row.is_active is False

    async def test_enable_disabled_users(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u = await _seed_user(db_manager, is_active=False)
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [u.id], "action": "enable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == [u.id]
        row = await _get_user(db_manager, u.id)
        assert row is not None and row.is_active is True


# ============================================================
# Delete
# ============================================================


class TestDelete:
    async def test_bulk_delete_succeeds(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u1 = await _seed_user(db_manager)
        u2 = await _seed_user(db_manager)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [u1.id, u2.id], "action": "delete"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["succeeded"]) == sorted([u1.id, u2.id])
        assert body["failed"] == []

        assert await _get_user(db_manager, u1.id) is None
        assert await _get_user(db_manager, u2.id) is None

    async def test_bulk_delete_cascades_conversations(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u = await _seed_user(db_manager)
        conv_id = await _seed_conv(db_manager, u.id)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [u.id], "action": "delete"},
        )
        assert resp.status_code == 200

        # Conv should be cascade-deleted
        async with db_manager.session() as s:
            result = await s.execute(
                select(Conversation).where(Conversation.id == conv_id)
            )
            assert result.scalar_one_or_none() is None


# ============================================================
# set_department
# ============================================================


class TestSetDepartment:
    async def test_set_department_assigns(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        dept = await _seed_department(db_manager)
        u1 = await _seed_user(db_manager, department_id=None)
        u2 = await _seed_user(db_manager, department_id=None)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={
                "ids": [u1.id, u2.id],
                "action": "set_department",
                "payload": {"department_id": dept.id},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["succeeded"]) == sorted([u1.id, u2.id])

        for uid in (u1.id, u2.id):
            row = await _get_user(db_manager, uid)
            assert row is not None and row.department_id == dept.id

    async def test_set_department_null_clears(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        dept = await _seed_department(db_manager)
        u = await _seed_user(db_manager, department_id=dept.id)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={
                "ids": [u.id],
                "action": "set_department",
                "payload": {"department_id": None},
            },
        )
        assert resp.status_code == 200
        row = await _get_user(db_manager, u.id)
        assert row is not None and row.department_id is None

    async def test_set_department_invalid_id_rejects_batch(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u = await _seed_user(db_manager, department_id=None)
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={
                "ids": [u.id],
                "action": "set_department",
                "payload": {"department_id": "dept-does-not-exist"},
            },
        )
        assert resp.status_code == 400
        # User unchanged
        row = await _get_user(db_manager, u.id)
        assert row is not None and row.department_id is None

    async def test_set_department_missing_payload_rejects(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": ["u-x"], "action": "set_department"},
        )
        assert resp.status_code == 400

    async def test_set_department_payload_missing_field_rejects(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": ["u-x"], "action": "set_department", "payload": {}},
        )
        assert resp.status_code == 400

    async def test_integrity_error_one_row_does_not_poison_subsequent(
        self,
        admin_client: AsyncClient,
        db_manager: DatabaseManager,
        monkeypatch,
    ):
        """
        Reviewer P1 regression: 模拟 set_department 的 dept 在 loop 外预校验
        通过后被另一个 admin 删了 → 中间一行 update 撞 FK，应当 rollback
        session 让后续行正常处理，而不是把整批拖进 PendingRollbackError。
        """
        dept = await _seed_department(db_manager)
        u_first = await _seed_user(db_manager)
        u_bad = await _seed_user(db_manager)
        u_last = await _seed_user(db_manager)

        real_update = UserRepository.update

        async def faulty_update(self, entity):
            if entity.id == u_bad.id:
                raise IntegrityError("simulated FK violation", None, Exception())
            return await real_update(self, entity)

        monkeypatch.setattr(UserRepository, "update", faulty_update)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={
                "ids": [u_first.id, u_bad.id, u_last.id],
                "action": "set_department",
                "payload": {"department_id": dept.id},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["succeeded"]) == sorted([u_first.id, u_last.id])
        assert body["failed"] == [{"id": u_bad.id, "reason": "internal_error"}]

        # u_last 真的被 commit — 证明 session 没被前面那条污染
        first = await _get_user(db_manager, u_first.id)
        last = await _get_user(db_manager, u_last.id)
        assert first is not None and first.department_id == dept.id
        assert last is not None and last.department_id == dept.id

    async def test_unknown_exception_bubbles_loudly(
        self,
        admin_client: AsyncClient,
        db_manager: DatabaseManager,
        monkeypatch,
    ):
        """
        非 IntegrityError 的异常（编程错误 / 真正的基础设施故障）不被路由静默 catch，
        而是冒泡到 app 边界。RequestContextMiddleware（app 级 catch-all）在边界上
        logger.exception 落完整堆栈（loud failure 落日志），并返回带 request_id 的
        脱敏 500 —— 而不是吞成 per-id internal_error 假装好了。

        注:本测试此前断言「异常裸传出 ASGI app」(ASGITransport raise_app_exceptions),
        那是 request-id 中间件落地前的行为。现在 loud 体现在服务端日志,客户端拿到
        干净的可定位 500。
        """
        dept = await _seed_department(db_manager)
        u = await _seed_user(db_manager)

        async def boom_update(self, entity):
            raise RuntimeError("unexpected programming error")

        monkeypatch.setattr(UserRepository, "update", boom_update)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={
                "ids": [u.id],
                "action": "set_department",
                "payload": {"department_id": dept.id},
            },
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"] == "Internal server error"  # 脱敏,不泄漏内部细节
        assert "unexpected programming error" not in resp.text
        # 可回传定位码:body + 响应头都带且一致
        assert body["request_id"].startswith("req-")
        assert body["request_id"] == resp.headers.get("X-Request-ID")


# ============================================================
# Self-protection + not-found
# ============================================================


class TestSelfProtection:
    async def test_admin_cannot_act_on_self(
        self, admin_client: AsyncClient, test_admin: User, db_manager: DatabaseManager
    ):
        other = await _seed_user(db_manager, is_active=True)

        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [test_admin.id, other.id], "action": "disable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == [other.id]
        assert body["failed"] == [{"id": test_admin.id, "reason": "forbidden_self"}]

        # Admin's own row untouched
        row = await _get_user(db_manager, test_admin.id)
        assert row is not None and row.is_active is True

    async def test_self_id_in_delete_rejected_per_id(
        self, admin_client: AsyncClient, test_admin: User
    ):
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [test_admin.id], "action": "delete"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == []
        assert body["failed"] == [{"id": test_admin.id, "reason": "forbidden_self"}]


class TestNotFound:
    async def test_nonexistent_id_returns_not_found_per_action(
        self, admin_client: AsyncClient
    ):
        for action in ("disable", "enable", "delete"):
            resp = await admin_client.post(
                "/api/v1/admin/users/bulk-action",
                json={"ids": ["u-ghost"], "action": action},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["succeeded"] == []
            assert body["failed"] == [{"id": "u-ghost", "reason": "not_found"}]


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    async def test_duplicate_ids_deduped(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u = await _seed_user(db_manager, is_active=True)
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [u.id, u.id, u.id], "action": "disable"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["succeeded"] == [u.id]
        assert body["failed"] == []

    async def test_empty_ids_rejected(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": [], "action": "disable"},
        )
        assert resp.status_code == 422

    async def test_over_capacity_rejected(self, admin_client: AsyncClient):
        too_many = [f"u-{i}" for i in range(201)]
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": too_many, "action": "disable"},
        )
        assert resp.status_code == 422

    async def test_unknown_action_rejected(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users/bulk-action",
            json={"ids": ["u-x"], "action": "format_disk"},
        )
        assert resp.status_code == 422


# ============================================================
# Bulk impact
# ============================================================


class TestBulkImpact:
    async def test_impact_aggregates_conversations_across_users(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u1 = await _seed_user(db_manager)
        u2 = await _seed_user(db_manager)
        # u1 has 3 convs, u2 has 2 convs
        for _ in range(3):
            await _seed_conv(db_manager, u1.id)
        for _ in range(2):
            await _seed_conv(db_manager, u2.id)

        resp = await admin_client.get(
            "/api/v1/admin/users/bulk-impact",
            params=[("ids", u1.id), ("ids", u2.id)],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_count"] == 2
        assert body["conversation_count"] == 5

    async def test_impact_dedups_user_ids(
        self, admin_client: AsyncClient, db_manager: DatabaseManager
    ):
        u = await _seed_user(db_manager)
        await _seed_conv(db_manager, u.id)

        resp = await admin_client.get(
            "/api/v1/admin/users/bulk-impact",
            params=[("ids", u.id), ("ids", u.id), ("ids", u.id)],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_count"] == 1
        assert body["conversation_count"] == 1

    async def test_impact_unknown_users_zero_count(
        self, admin_client: AsyncClient
    ):
        resp = await admin_client.get(
            "/api/v1/admin/users/bulk-impact?ids=u-ghost-1&ids=u-ghost-2",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_count"] == 2
        assert body["conversation_count"] == 0

    async def test_impact_capacity_exceeded(self, admin_client: AsyncClient):
        params = [("ids", f"u-{i}") for i in range(201)]
        resp = await admin_client.get(
            "/api/v1/admin/users/bulk-impact", params=params
        )
        assert resp.status_code == 422

    async def test_impact_does_not_resolve_bulk_action_route(
        self, admin_client: AsyncClient
    ):
        """
        Sanity: GET /users/bulk-impact must NOT be routed as
        GET /users/{user_id} with user_id='bulk-impact' (would 404).
        """
        # A successful 200 with the impact shape proves the literal-path route won.
        resp = await admin_client.get(
            "/api/v1/admin/users/bulk-impact?ids=u-anything",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "user_count" in body
        assert "conversation_count" in body
