"""
PR2b — User hard-delete endpoint tests.

Covers:
- DELETE /api/v1/admin/users/{id} — happy path, FK CASCADE, self-protection
- GET /api/v1/admin/users/{id} — single fetch
- GET /api/v1/admin/users/{id}/impact — conversation count for confirmation UI
- PUT /api/v1/admin/users/{id} — self-protection on role / is_active
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.auth import hash_password
from db.models import (
    Artifact,
    ArtifactSession,
    Conversation,
    Message,
    MessageEvent,
    User,
)
from repositories.conversation_repo import ConversationRepository
from repositories.user_repo import UserRepository


# ============================================================
# Local fixtures
# ============================================================


@pytest.fixture
async def second_admin(user_repo: UserRepository) -> User:
    """A second admin so test_admin can delete an admin without hitting self-protection."""
    admin = User(
        id=str(uuid.uuid4()),
        username=f"admin2-{uuid.uuid4().hex[:6]}",
        hashed_password=hash_password("pass1234"),
        role="admin",
        is_active=True,
    )
    return await user_repo.add(admin)


async def _seed_user_with_conversation(
    db_session: AsyncSession, user: User
) -> tuple[str, str]:
    """Create a conversation + message + event + artifact owned by `user`. Returns (conv_id, msg_id)."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    msg_id = f"msg-{uuid.uuid4().hex}"

    # Phase 1: parent rows must commit before children's FK can resolve
    db_session.add(Conversation(id=conv_id, user_id=user.id, title="Seeded"))
    db_session.add(ArtifactSession(id=conv_id))
    db_session.add(Message(
        id=msg_id,
        conversation_id=conv_id,
        user_input="hello",
        response="world",
    ))
    await db_session.flush()
    await db_session.commit()

    # Phase 2: dependent rows now have valid FK targets
    db_session.add(Artifact(
        id=f"art-{uuid.uuid4().hex[:8]}",
        session_id=conv_id,
        content_type="text/markdown",
        title="Seeded artifact",
        content="content",
        current_version=1,
    ))
    db_session.add(MessageEvent(
        message_id=msg_id,
        event_type="llm_complete",
        agent_name="lead_agent",
        data={"content": "world"},
    ))
    await db_session.flush()
    await db_session.commit()
    return conv_id, msg_id


# ============================================================
# DELETE happy path / cascade
# ============================================================


class TestDeleteUser:

    async def test_delete_user_success_returns_204(
        self, admin_client: AsyncClient, test_user: User
    ):
        resp = await admin_client.delete(f"/api/v1/admin/users/{test_user.id}")
        assert resp.status_code == 204

    async def test_delete_user_removes_row(
        self,
        admin_client: AsyncClient,
        test_user: User,
        db_manager,
    ):
        await admin_client.delete(f"/api/v1/admin/users/{test_user.id}")

        # Use a fresh session — db_session fixture's open transaction may not
        # see commits from the request handler's session.
        async with db_manager.session() as s:
            result = await s.execute(
                select(User).where(User.id == test_user.id)
            )
            assert result.scalar_one_or_none() is None

    async def test_delete_user_cascades_conversations(
        self,
        admin_client: AsyncClient,
        test_user: User,
        db_session: AsyncSession,
        db_manager,
    ):
        """FK CASCADE: 删用户连带删 conversation / messages / events / artifacts。"""
        conv_id, msg_id = await _seed_user_with_conversation(db_session, test_user)

        resp = await admin_client.delete(f"/api/v1/admin/users/{test_user.id}")
        assert resp.status_code == 204

        # Use fresh session to avoid stale view from db_session's open txn
        async with db_manager.session() as s:
            async def _count(model, where):
                result = await s.execute(select(model).where(where))
                return len(result.scalars().all())

            assert await _count(Conversation, Conversation.id == conv_id) == 0
            assert await _count(Message, Message.id == msg_id) == 0
            assert await _count(MessageEvent, MessageEvent.message_id == msg_id) == 0
            assert await _count(Artifact, Artifact.session_id == conv_id) == 0
            assert await _count(ArtifactSession, ArtifactSession.id == conv_id) == 0

    async def test_delete_self_forbidden(
        self, admin_client: AsyncClient, test_admin: User
    ):
        """admin 不能删自己（防误锁 + 配合 PUT self-guard 保住至少 1 个活跃 admin）"""
        resp = await admin_client.delete(f"/api/v1/admin/users/{test_admin.id}")
        assert resp.status_code == 403
        assert "yourself" in resp.json()["detail"].lower()

    async def test_delete_another_admin_allowed(
        self,
        admin_client: AsyncClient,
        second_admin: User,
        db_manager,
    ):
        """删别的 admin 不受限 —— 只要不是删自己即可。"""
        resp = await admin_client.delete(f"/api/v1/admin/users/{second_admin.id}")
        assert resp.status_code == 204
        async with db_manager.session() as s:
            result = await s.execute(
                select(User).where(User.id == second_admin.id)
            )
            assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_returns_404(self, admin_client: AsyncClient):
        resp = await admin_client.delete(f"/api/v1/admin/users/nonexistent-{uuid.uuid4().hex}")
        assert resp.status_code == 404

    async def test_delete_as_regular_user_forbidden(
        self, client: AsyncClient, test_user: User, second_admin: User
    ):
        resp = await client.delete(f"/api/v1/admin/users/{second_admin.id}")
        assert resp.status_code == 403

    async def test_delete_unauthenticated(
        self, anon_client: AsyncClient, test_user: User
    ):
        resp = await anon_client.delete(f"/api/v1/admin/users/{test_user.id}")
        assert resp.status_code == 401


# ============================================================
# Self-protection on PUT
# ============================================================


class TestUpdateSelfProtection:

    async def test_admin_cannot_demote_self(
        self, admin_client: AsyncClient, test_admin: User
    ):
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_admin.id}",
            json={"role": "user"},
        )
        assert resp.status_code == 403
        assert "your own role" in resp.json()["detail"].lower()

    async def test_admin_cannot_disable_self(
        self, admin_client: AsyncClient, test_admin: User
    ):
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_admin.id}",
            json={"is_active": False},
        )
        assert resp.status_code == 403
        assert "your own active" in resp.json()["detail"].lower()

    async def test_admin_cannot_change_own_password_via_admin_endpoint(
        self, admin_client: AsyncClient, test_admin: User
    ):
        """
        防止 admin 在后台绕过 /me/password 的 current_password 校验。
        token 被盗场景下，攻击者持 token 也不能直接改 admin 自己密码。
        """
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_admin.id}",
            json={"password": "Newpass1234!"},
        )
        assert resp.status_code == 403
        assert "/me/password" in resp.json()["detail"].lower()

    async def test_admin_can_change_own_display_name(
        self, admin_client: AsyncClient, test_admin: User
    ):
        """display_name / password 这种"非破坏性"字段允许改自己。"""
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_admin.id}",
            json={"display_name": "Captain"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Captain"

    async def test_admin_can_demote_another_admin(
        self,
        admin_client: AsyncClient,
        second_admin: User,
    ):
        """非自身的 demote 仍然允许（self-protection 只防自己）。"""
        resp = await admin_client.put(
            f"/api/v1/admin/users/{second_admin.id}",
            json={"role": "user"},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] == "user"

    async def test_admin_can_disable_another_admin(
        self,
        admin_client: AsyncClient,
        second_admin: User,
    ):
        resp = await admin_client.put(
            f"/api/v1/admin/users/{second_admin.id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_admin_setting_same_role_to_self_is_noop(
        self, admin_client: AsyncClient, test_admin: User
    ):
        """传相同 role 给自己应该被允许（值未变，不算修改）。"""
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_admin.id}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200


# ============================================================
# GET single / impact endpoints
# ============================================================


class TestGetUser:

    async def test_get_user_as_admin(self, admin_client: AsyncClient, test_user: User):
        resp = await admin_client.get(f"/api/v1/admin/users/{test_user.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == test_user.id
        assert body["username"] == test_user.username

    async def test_get_user_not_found(self, admin_client: AsyncClient):
        resp = await admin_client.get(f"/api/v1/admin/users/nope-{uuid.uuid4().hex}")
        assert resp.status_code == 404

    async def test_get_user_as_regular_user_forbidden(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.get(f"/api/v1/admin/users/{test_user.id}")
        assert resp.status_code == 403


class TestUserImpact:

    async def test_impact_zero_for_user_without_conversations(
        self, admin_client: AsyncClient, test_user: User
    ):
        resp = await admin_client.get(f"/api/v1/admin/users/{test_user.id}/impact")
        assert resp.status_code == 200
        assert resp.json() == {"conversation_count": 0}

    async def test_impact_counts_owned_conversations(
        self,
        admin_client: AsyncClient,
        test_user: User,
        db_session: AsyncSession,
    ):
        await _seed_user_with_conversation(db_session, test_user)
        await _seed_user_with_conversation(db_session, test_user)

        resp = await admin_client.get(f"/api/v1/admin/users/{test_user.id}/impact")
        assert resp.status_code == 200
        assert resp.json()["conversation_count"] == 2

    async def test_impact_user_not_found(self, admin_client: AsyncClient):
        resp = await admin_client.get(
            f"/api/v1/admin/users/nope-{uuid.uuid4().hex}/impact"
        )
        assert resp.status_code == 404

    async def test_impact_as_regular_user_forbidden(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.get(f"/api/v1/admin/users/{test_user.id}/impact")
        assert resp.status_code == 403
