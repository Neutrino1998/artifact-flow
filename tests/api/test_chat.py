"""
Chat API integration tests.

Data seeding strategy: POST /chat triggers engine execution, so we seed data
directly via repository methods through db_manager.session().
After commit, API's independent session can see the data.
"""

import asyncio
import json
import uuid
from typing import Tuple, List

import pytest
from httpx import AsyncClient

from config import config

from db.models import User
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from repositories.artifact_repo import ArtifactRepository
from api.services.execution_runner import ExecutionRunner


async def _seed_conv_with_blob(
    db_manager: DatabaseManager, user_id: str, blob_size: int, title: str = "blob conv"
) -> str:
    """Seed a conversation owning one blob-backed artifact of `blob_size` bytes."""
    async with db_manager.session() as session:
        conv_id = f"conv-{uuid.uuid4().hex}"
        await ConversationRepository(session).create_conversation(
            conversation_id=conv_id, title=title, user_id=user_id
        )
        await ArtifactRepository(session).create_artifact(
            session_id=conv_id,
            artifact_id=f"art-{uuid.uuid4().hex}",
            content_type="application/octet-stream",
            title="blob",
            content="",
            blob=b"x" * blob_size,
        )
    return conv_id


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

        await repo.add_message(conv_id, msg1_id, "first message")
        await repo.update_response(msg1_id, "first response")

        await repo.add_message(
            conv_id, msg2_id, "second message", parent_id=msg1_id
        )
        await repo.update_response(msg2_id, "second response")

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

        await repo.add_message(conv_id, root_id, "root")
        await repo.add_message(conv_id, child_a_id, "branch A", parent_id=root_id)
        await repo.add_message(conv_id, child_b_id, "branch B", parent_id=root_id)

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


# ============================================================
# Resume interrupt idempotency
# ============================================================


class TestResumeInterrupt:

    async def test_resume_already_resolved_returns_409(
        self,
        client: AsyncClient,
        app,
        seed_conversation: Tuple[str, List[str]],
    ):
        """Second resume on the same interrupt should return 409, not 404."""
        conv_id, msg_ids = seed_conversation
        message_id = msg_ids[0]

        # Get the ExecutionRunner injected into the app
        from api.dependencies import get_execution_runner
        runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()

        # Simulate an interrupt that the engine would create (bypass wait_for_interrupt
        # which would block; directly populate internal state)
        from api.services.runtime_store import _InterruptState
        runner.store._interrupts[message_id] = _InterruptState(interrupt_data={
            "tool": "web_search",
            "params": {"query": "test"},
        })

        # First resume — should succeed (resolve the interrupt)
        resp = await client.post(f"/api/v1/chat/{conv_id}/resume", json={
            "message_id": message_id,
            "approved": True,
            "always_allow": False,
        })
        assert resp.status_code == 200

        # Second resume — same interrupt already resolved → 409
        resp = await client.post(f"/api/v1/chat/{conv_id}/resume", json={
            "message_id": message_id,
            "approved": True,
            "always_allow": False,
        })
        assert resp.status_code == 409

    async def test_resume_no_interrupt_returns_404(
        self,
        client: AsyncClient,
        seed_conversation: Tuple[str, List[str]],
    ):
        """Resume with no pending interrupt should return 404."""
        conv_id, msg_ids = seed_conversation
        message_id = msg_ids[0]

        resp = await client.post(f"/api/v1/chat/{conv_id}/resume", json={
            "message_id": message_id,
            "approved": True,
            "always_allow": False,
        })
        assert resp.status_code == 404


class TestChatInputCap:
    """Input-size guardrail: oversized user_input is rejected at the boundary
    (before any execution) so it can't overflow the first LLM call."""

    async def test_oversized_user_input_rejected(self, client: AsyncClient):
        # Over-cap is rejected by the schema validator inside the endpoint,
        # before conversation creation / submit — so no background engine task
        # is spawned by this request.
        huge = "x" * (config.MAX_MESSAGE_CHARS + 1)
        resp = await client.post(
            "/api/v1/chat",
            files={"payload": (None, json.dumps({"user_input": huge}))},
        )
        assert resp.status_code == 422

    async def test_too_many_attachments_rejected(self, client: AsyncClient):
        # Count cap is enforced at the top of the handler (before conversation
        # creation / conversion), so no background engine task is spawned.
        n = config.MAX_CHAT_ATTACHMENTS + 1
        parts = [("payload", (None, json.dumps({"user_input": "hi"})))]
        for i in range(n):
            parts.append(("files", (f"f{i}.txt", b"x", "text/plain")))
        resp = await client.post("/api/v1/chat", files=parts)
        assert resp.status_code == 422


class TestStorageQuota:
    """Per-user blob quota: the 413 gate, the /storage gauge, and list upload_bytes."""

    async def test_storage_usage_reports_used_and_quota(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        await _seed_conv_with_blob(db_manager, test_user.id, 300)
        await _seed_conv_with_blob(db_manager, test_user.id, 200)

        resp = await client.get("/api/v1/chat/storage")
        assert resp.status_code == 200
        body = resp.json()
        assert body["used_bytes"] == 500
        assert body["quota_bytes"] == config.ARTIFACT_USER_QUOTA_BYTES

    async def test_storage_usage_isolated_per_user(
        self,
        client: AsyncClient,
        admin_client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
        test_admin: User,
    ):
        await _seed_conv_with_blob(db_manager, test_user.id, 300)
        await _seed_conv_with_blob(db_manager, test_admin.id, 999)

        assert (await client.get("/api/v1/chat/storage")).json()["used_bytes"] == 300
        assert (await admin_client.get("/api/v1/chat/storage")).json()["used_bytes"] == 999

    async def test_list_exposes_per_conversation_upload_bytes(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        conv_id = await _seed_conv_with_blob(db_manager, test_user.id, 420)

        resp = await client.get("/api/v1/chat")
        assert resp.status_code == 200
        row = next(c for c in resp.json()["conversations"] if c["id"] == conv_id)
        assert row["upload_bytes"] == 420

    async def test_upload_over_quota_rejected_with_413(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
        monkeypatch,
    ):
        # Shrink the quota so a tiny seeded blob + a tiny upload trips it. The
        # gate reads config live, so monkeypatching the attr is enough.
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 1000)
        await _seed_conv_with_blob(db_manager, test_user.id, 900)
        before = (await client.get("/api/v1/chat")).json()["total"]

        # A .bin attachment takes the blob path → counts toward the quota.
        # 900 (existing) + 200 (incoming) = 1100 > 1000 → reject before submit.
        parts = [
            ("payload", (None, json.dumps({"user_input": "hi"}))),
            ("files", ("data.bin", b"x" * 200, "application/octet-stream")),
        ]
        resp = await client.post("/api/v1/chat", files=parts)
        assert resp.status_code == 413

        # Rejected before conversation creation → no ghost conversation.
        after = (await client.get("/api/v1/chat")).json()["total"]
        assert after == before

    async def test_quota_disabled_when_zero_does_not_reject(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
        monkeypatch,
    ):
        # 0 = unlimited: the gate must short-circuit BEFORE the usage query, even
        # with an over-sized existing footprint. We assert via the /storage gauge
        # that quota_bytes surfaces 0 (the gate's disabled signal) — without
        # POSTing an upload, which would spawn the engine.
        monkeypatch.setattr(config, "ARTIFACT_USER_QUOTA_BYTES", 0)
        await _seed_conv_with_blob(db_manager, test_user.id, 5000)

        body = (await client.get("/api/v1/chat/storage")).json()
        assert body["quota_bytes"] == 0
        assert body["used_bytes"] == 5000


class TestChatUploadAtomicity:
    """A bad file in a multi-file batch must abort with zero DB state — no ghost
    conversation, no orphan artifacts. The handler converts ALL attachments
    (phase 1, pure, no DB) before creating the conversation or any artifact
    (phase 2), so an unsupported file fails before any write. Regression for the
    old interleaved convert+commit loop, which created the conversation up front
    and committed the files preceding the bad one, leaving a ghost conversation."""

    async def test_bad_file_aborts_batch_with_no_db_state(self, client: AsyncClient):
        before = (await client.get("/api/v1/chat")).json()["total"]

        # New conversation (no conversation_id). Good file FIRST so the old code
        # would have created the conversation + committed good.txt before the
        # bad file raised 422. 上传翻转(2026-06-11)后格式不再拒,仍 422 的
        # 触发器 = 损坏 png(识图路由闸:png/jpg 扩展但 Pillow 探不出合法图)。
        parts = [
            ("payload", (None, json.dumps({"user_input": "hi"}))),
            ("files", ("good.txt", b"hello", "text/plain")),
            ("files", ("broken.png", b"\x89PNG\r\n\x1a\n" + b"garbage", "image/png")),
        ]
        resp = await client.post("/api/v1/chat", files=parts)
        assert resp.status_code == 422

        # No conversation created (list returns ALL of the user's convs, with no
        # message-count filter). No conversation ⇒ no orphan artifacts either,
        # since an artifact FK-depends on its (absent) conversation's session.
        after = (await client.get("/api/v1/chat")).json()["total"]
        assert after == before
