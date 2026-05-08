"""
PR5b — Bulk-delete conversations endpoint integration tests.

Covers POST /api/v1/chat/bulk-delete:
- Auth (anon 401)
- Happy path: own convs all deleted
- Cross-user id → reason='not_found' (404-not-403 policy, no existence leak)
- Nonexistent id → reason='not_found'
- Mixed batch (own + foreign + nonexistent) → partial deleted/failed split
- Duplicate id within payload → deduped, single deletion
- Capacity over MAX_BULK_DELETE_IDS → 422 from pydantic max_length

Engine fail-soft on active execution is covered by tests/test_controller_skip_on_delete.py
(PR2a layer); this file just verifies the bulk endpoint correctly removes the row and
the response shape is right.
"""

import uuid
from typing import List

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from db.database import DatabaseManager
from db.models import Conversation, User
from repositories.conversation_repo import ConversationRepository


# ============================================================
# Helpers
# ============================================================


async def _seed_conv(db_manager: DatabaseManager, user_id: str, title: str = "seed") -> str:
    conv_id = f"conv-{uuid.uuid4().hex}"
    async with db_manager.session() as s:
        repo = ConversationRepository(s)
        await repo.create_conversation(
            conversation_id=conv_id, title=title, user_id=user_id,
        )
    return conv_id


async def _conv_exists(db_manager: DatabaseManager, conv_id: str) -> bool:
    async with db_manager.session() as s:
        result = await s.execute(select(Conversation).where(Conversation.id == conv_id))
        return result.scalar_one_or_none() is not None


# ============================================================
# Auth
# ============================================================


class TestAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/chat/bulk-delete", json={"ids": ["conv-x"]}
        )
        assert resp.status_code == 401


# ============================================================
# Happy path
# ============================================================


class TestHappyPath:
    async def test_delete_own_convs_all_succeed(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        ids = [await _seed_conv(db_manager, test_user.id) for _ in range(3)]

        resp = await client.post("/api/v1/chat/bulk-delete", json={"ids": ids})
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["deleted"]) == sorted(ids)
        assert body["failed"] == []

        for conv_id in ids:
            assert not await _conv_exists(db_manager, conv_id)

    async def test_single_id(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        conv_id = await _seed_conv(db_manager, test_user.id)
        resp = await client.post("/api/v1/chat/bulk-delete", json={"ids": [conv_id]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == [conv_id]
        assert body["failed"] == []
        assert not await _conv_exists(db_manager, conv_id)


# ============================================================
# Failure modes
# ============================================================


class TestFailureModes:
    async def test_nonexistent_id_returns_not_found(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/chat/bulk-delete", json={"ids": ["conv-does-not-exist"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == []
        assert len(body["failed"]) == 1
        assert body["failed"][0]["id"] == "conv-does-not-exist"
        assert body["failed"][0]["reason"] == "not_found"

    async def test_cross_user_id_returns_not_found_no_existence_leak(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_admin: User,
    ):
        # Conv owned by admin; regular user tries to delete it
        foreign_id = await _seed_conv(db_manager, test_admin.id)

        resp = await client.post(
            "/api/v1/chat/bulk-delete", json={"ids": [foreign_id]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == []
        assert body["failed"] == [{"id": foreign_id, "reason": "not_found"}]

        # Foreign conv still exists — wasn't actually deleted
        assert await _conv_exists(db_manager, foreign_id)

    async def test_mixed_batch_partial_split(
        self,
        client: AsyncClient,
        db_manager: DatabaseManager,
        test_user: User,
        test_admin: User,
    ):
        own_a = await _seed_conv(db_manager, test_user.id)
        own_b = await _seed_conv(db_manager, test_user.id)
        foreign = await _seed_conv(db_manager, test_admin.id)
        ghost = "conv-ghost"

        resp = await client.post(
            "/api/v1/chat/bulk-delete",
            json={"ids": [own_a, foreign, own_b, ghost]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(body["deleted"]) == sorted([own_a, own_b])
        failed_ids = {f["id"]: f["reason"] for f in body["failed"]}
        assert failed_ids == {foreign: "not_found", ghost: "not_found"}

        assert not await _conv_exists(db_manager, own_a)
        assert not await _conv_exists(db_manager, own_b)
        assert await _conv_exists(db_manager, foreign)


# ============================================================
# Edge cases
# ============================================================


class TestEdgeCases:
    async def test_duplicate_ids_deduped(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        conv_id = await _seed_conv(db_manager, test_user.id)
        resp = await client.post(
            "/api/v1/chat/bulk-delete", json={"ids": [conv_id, conv_id, conv_id]}
        )
        assert resp.status_code == 200
        body = resp.json()
        # Dedup: deleted listed once, no spurious not_found for same id
        assert body["deleted"] == [conv_id]
        assert body["failed"] == []

    async def test_empty_ids_rejected(self, client: AsyncClient):
        resp = await client.post("/api/v1/chat/bulk-delete", json={"ids": []})
        assert resp.status_code == 422

    async def test_over_capacity_rejected(self, client: AsyncClient):
        too_many = [f"conv-{i}" for i in range(201)]
        resp = await client.post(
            "/api/v1/chat/bulk-delete", json={"ids": too_many}
        )
        assert resp.status_code == 422
