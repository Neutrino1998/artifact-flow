"""
Chat API integration tests.

Data seeding strategy: POST /chat triggers Graph, so we seed data
directly via repository methods through db_manager.session().
After commit, API's independent session can see the data.
"""

import uuid
from typing import Tuple, List

import pytest
from httpx import AsyncClient

from db.models import User
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository


# ============================================================
# Local fixtures
# ============================================================


@pytest.fixture
async def seed_conversation(
    db_manager: DatabaseManager, test_user: User
) -> Tuple[str, List[str]]:
    """
    Seed a conversation with messages directly via repository.

    Returns (conv_id, [msg1_id, msg2_id]).
    """
    async with db_manager.session() as session:
        repo = ConversationRepository(session)

        conv_id = f"conv-{uuid.uuid4().hex}"
        await repo.create_conversation(
            conversation_id=conv_id,
            title="Seeded Conversation",
            user_id=test_user.id,
        )

        msg1_id = f"msg-{uuid.uuid4().hex}"
        msg2_id = f"msg-{uuid.uuid4().hex}"

        await repo.add_message(conv_id, msg1_id, "first message", "thd-1")
        await repo.update_graph_response(msg1_id, "first response")

        await repo.add_message(
            conv_id, msg2_id, "second message", "thd-1", parent_id=msg1_id
        )
        await repo.update_graph_response(msg2_id, "second response")

    return conv_id, [msg1_id, msg2_id]


@pytest.fixture
async def seed_branched_conversation(
    db_manager: DatabaseManager, test_user: User
) -> Tuple[str, List[str]]:
    """
    Seed a conversation with a branch (root → child_a, root → child_b).

    Returns (conv_id, [root_id, child_a_id, child_b_id]).
    """
    async with db_manager.session() as session:
        repo = ConversationRepository(session)

        conv_id = f"conv-{uuid.uuid4().hex}"
        await repo.create_conversation(
            conversation_id=conv_id,
            title="Branched",
            user_id=test_user.id,
        )

        root_id = f"msg-{uuid.uuid4().hex}"
        child_a_id = f"msg-{uuid.uuid4().hex}"
        child_b_id = f"msg-{uuid.uuid4().hex}"

        await repo.add_message(conv_id, root_id, "root", "thd-1")
        await repo.add_message(conv_id, child_a_id, "branch A", "thd-1", parent_id=root_id)
        await repo.add_message(conv_id, child_b_id, "branch B", "thd-1", parent_id=root_id)

    return conv_id, [root_id, child_a_id, child_b_id]


# ============================================================
# List conversations
# ============================================================


class TestListConversations:

    async def test_list_empty(self, client: AsyncClient):
        resp = await client.get("/api/v1/chat")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["has_more"] is False
        assert body["conversations"] == []

    async def test_list_own_only(
        self,
        client: AsyncClient,
        admin_client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
        test_admin: User,
    ):
        # Seed conversations for both users
        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            await repo.create_conversation(
                f"conv-{uuid.uuid4().hex}", user_id=test_user.id
            )
            await repo.create_conversation(
                f"conv-{uuid.uuid4().hex}", user_id=test_admin.id
            )

        # Regular user should only see their own
        resp = await client.get("/api/v1/chat")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # Admin should only see their own
        resp = await admin_client.get("/api/v1/chat")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_list_pagination(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
    ):
        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            for _ in range(3):
                await repo.create_conversation(
                    f"conv-{uuid.uuid4().hex}", user_id=test_user.id
                )

        resp = await client.get("/api/v1/chat?limit=2&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["conversations"]) == 2
        assert body["has_more"] is True

    async def test_list_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/chat")
        assert resp.status_code == 401


# ============================================================
# Conversation detail
# ============================================================


class TestConversationDetail:

    async def test_get_detail(
        self, client: AsyncClient, seed_conversation: Tuple[str, List[str]]
    ):
        conv_id, msg_ids = seed_conversation
        resp = await client.get(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 200

        body = resp.json()
        assert body["id"] == conv_id
        assert body["active_branch"] is not None
        assert body["session_id"] == conv_id
        assert len(body["messages"]) == 2

    async def test_get_cross_user_returns_404(
        self,
        admin_client: AsyncClient,
        seed_conversation: Tuple[str, List[str]],
    ):
        conv_id, _ = seed_conversation
        # admin_client tries to access test_user's conversation
        resp = await admin_client.get(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 404

    async def test_get_nonexistent(self, client: AsyncClient):
        resp = await client.get("/api/v1/chat/nonexistent-conv-id")
        assert resp.status_code == 404

    async def test_get_messages_have_children(
        self,
        client: AsyncClient,
        seed_branched_conversation: Tuple[str, List[str]],
    ):
        conv_id, msg_ids = seed_branched_conversation
        root_id = msg_ids[0]

        resp = await client.get(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 200

        body = resp.json()
        # Find root message and verify its children
        root_msg = next(m for m in body["messages"] if m["id"] == root_id)
        assert len(root_msg["children"]) == 2


# ============================================================
# Delete conversation
# ============================================================


class TestDeleteConversation:

    async def test_delete_success(
        self, client: AsyncClient, seed_conversation: Tuple[str, List[str]]
    ):
        conv_id, _ = seed_conversation
        resp = await client.delete(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 200

        # Should be gone
        resp = await client.get(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 404

    async def test_delete_cross_user(
        self,
        admin_client: AsyncClient,
        seed_conversation: Tuple[str, List[str]],
    ):
        conv_id, _ = seed_conversation
        resp = await admin_client.delete(f"/api/v1/chat/{conv_id}")
        assert resp.status_code == 404

    async def test_delete_nonexistent(self, client: AsyncClient):
        resp = await client.delete("/api/v1/chat/nonexistent-conv-id")
        assert resp.status_code == 404

    async def test_delete_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.delete("/api/v1/chat/some-conv-id")
        assert resp.status_code == 401
