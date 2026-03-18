"""
Chat endpoint integration tests for inject, cancel, compact, and events APIs.

Uses API fixtures with simulated active tasks.
"""

import asyncio
import uuid
from typing import Tuple, List

import pytest
from httpx import AsyncClient

from db.models import User
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from api.services.task_manager import TaskManager
from api.dependencies import get_task_manager
import api.dependencies as deps


# ============================================================
# Helpers
# ============================================================


async def _seed_conversation(
    db_manager: DatabaseManager, user_id: str
) -> Tuple[str, List[str]]:
    """Seed a conversation with 2 messages. Returns (conv_id, [msg1_id, msg2_id])."""
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        conv_id = f"conv-{uuid.uuid4().hex}"
        await repo.create_conversation(conv_id, title="Test Conv", user_id=user_id)

        msg1_id = f"msg-{uuid.uuid4().hex}"
        msg2_id = f"msg-{uuid.uuid4().hex}"

        await repo.add_message(conv_id, msg1_id, "first input")
        await repo.update_response(msg1_id, "first response")
        await repo.add_message(conv_id, msg2_id, "second input", parent_id=msg1_id)
        await repo.update_response(msg2_id, "second response")

    return conv_id, [msg1_id, msg2_id]


async def _simulate_active_task(app, conv_id: str, msg_id: str) -> asyncio.Event:
    """
    Register a reservation and submit a sleeping coroutine to simulate an active task.
    Returns a blocker event that can be set to let the task complete.
    """
    task_manager: TaskManager = app.dependency_overrides[get_task_manager]()
    task_manager.try_reserve_conversation(conv_id, msg_id)

    blocker = asyncio.Event()

    async def sleeping():
        await blocker.wait()

    await task_manager.submit(msg_id, sleeping())
    return blocker


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
async def conv_with_messages(
    db_manager: DatabaseManager, test_user: User
) -> Tuple[str, List[str]]:
    return await _seed_conversation(db_manager, test_user.id)


# ============================================================
# TestInject
# ============================================================


class TestInject:

    async def test_inject_success(
        self, client: AsyncClient, app, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages
        blocker = await _simulate_active_task(app, conv_id, msg_ids[0])

        resp = await client.post(
            f"/api/v1/chat/{conv_id}/inject",
            json={"content": "additional input"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["message_id"] == msg_ids[0]
        assert "stream" in body["stream_url"]

        blocker.set()

    async def test_inject_no_active_execution(
        self, client: AsyncClient, conv_with_messages
    ):
        conv_id, _ = conv_with_messages
        resp = await client.post(
            f"/api/v1/chat/{conv_id}/inject",
            json={"content": "test"},
        )
        assert resp.status_code == 409

    async def test_inject_cross_user(
        self, admin_client: AsyncClient, app, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages
        blocker = await _simulate_active_task(app, conv_id, msg_ids[0])

        resp = await admin_client.post(
            f"/api/v1/chat/{conv_id}/inject",
            json={"content": "test"},
        )
        assert resp.status_code == 404

        blocker.set()

    async def test_inject_unauthenticated(
        self, anon_client: AsyncClient, conv_with_messages
    ):
        conv_id, _ = conv_with_messages
        resp = await anon_client.post(
            f"/api/v1/chat/{conv_id}/inject",
            json={"content": "test"},
        )
        assert resp.status_code == 401


# ============================================================
# TestCancel
# ============================================================


class TestCancel:

    async def test_cancel_success(
        self, client: AsyncClient, app, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages
        blocker = await _simulate_active_task(app, conv_id, msg_ids[0])

        resp = await client.post(f"/api/v1/chat/{conv_id}/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["message_id"] == msg_ids[0]

        # Verify cancellation was requested
        tm: TaskManager = app.dependency_overrides[get_task_manager]()
        assert tm.is_cancelled(msg_ids[0]) is True

        blocker.set()

    async def test_cancel_no_active_execution(
        self, client: AsyncClient, conv_with_messages
    ):
        conv_id, _ = conv_with_messages
        resp = await client.post(f"/api/v1/chat/{conv_id}/cancel")
        assert resp.status_code == 409

    async def test_cancel_cross_user(
        self, admin_client: AsyncClient, app, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages
        blocker = await _simulate_active_task(app, conv_id, msg_ids[0])

        resp = await admin_client.post(f"/api/v1/chat/{conv_id}/cancel")
        assert resp.status_code == 404

        blocker.set()

    async def test_cancel_unauthenticated(
        self, anon_client: AsyncClient, conv_with_messages
    ):
        conv_id, _ = conv_with_messages
        resp = await anon_client.post(f"/api/v1/chat/{conv_id}/cancel")
        assert resp.status_code == 401


# ============================================================
# TestCompact
# ============================================================


class TestCompact:

    async def test_compact_success(
        self, client: AsyncClient, app, conv_with_messages
    ):
        conv_id, _ = conv_with_messages

        class FakeCompactionManager:
            async def trigger(self, conv_id, config):
                return True

        old = deps._compaction_manager
        deps._compaction_manager = FakeCompactionManager()
        try:
            resp = await client.post(f"/api/v1/chat/{conv_id}/compact")
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"
        finally:
            deps._compaction_manager = old

    async def test_compact_already_running(
        self, client: AsyncClient, app, conv_with_messages
    ):
        conv_id, _ = conv_with_messages

        class FakeCompactionManager:
            async def trigger(self, conv_id, config):
                return False

        old = deps._compaction_manager
        deps._compaction_manager = FakeCompactionManager()
        try:
            resp = await client.post(f"/api/v1/chat/{conv_id}/compact")
            assert resp.status_code == 409
        finally:
            deps._compaction_manager = old

    async def test_compact_service_unavailable(
        self, client: AsyncClient, app, conv_with_messages
    ):
        conv_id, _ = conv_with_messages

        old = deps._compaction_manager
        deps._compaction_manager = None
        try:
            resp = await client.post(f"/api/v1/chat/{conv_id}/compact")
            assert resp.status_code == 503
        finally:
            deps._compaction_manager = old

    async def test_compact_cross_user(
        self, admin_client: AsyncClient, app, conv_with_messages
    ):
        conv_id, _ = conv_with_messages

        class FakeCompactionManager:
            async def trigger(self, conv_id, config):
                return True

        old = deps._compaction_manager
        deps._compaction_manager = FakeCompactionManager()
        try:
            resp = await admin_client.post(f"/api/v1/chat/{conv_id}/compact")
            assert resp.status_code == 404
        finally:
            deps._compaction_manager = old


# ============================================================
# TestEvents
# ============================================================


class TestEvents:

    async def test_get_events_success(
        self, client: AsyncClient, db_manager: DatabaseManager, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages

        from repositories.message_event_repo import MessageEventRepository

        async with db_manager.session() as session:
            repo = MessageEventRepository(session)
            await repo.batch_create([
                {"message_id": msg_ids[0], "event_type": "agent_start", "agent_name": "lead_agent", "data": {"agent": "lead_agent"}},
                {"message_id": msg_ids[0], "event_type": "llm_complete", "agent_name": "lead_agent", "data": {"content": "hello"}},
            ])

        resp = await client.get(f"/api/v1/chat/{conv_id}/messages/{msg_ids[0]}/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["events"]) == 2

    async def test_get_events_with_type_filter(
        self, client: AsyncClient, db_manager: DatabaseManager, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages

        from repositories.message_event_repo import MessageEventRepository

        async with db_manager.session() as session:
            repo = MessageEventRepository(session)
            await repo.batch_create([
                {"message_id": msg_ids[0], "event_type": "agent_start", "agent_name": "lead_agent", "data": {}},
                {"message_id": msg_ids[0], "event_type": "tool_complete", "agent_name": "lead_agent", "data": {}},
            ])

        resp = await client.get(
            f"/api/v1/chat/{conv_id}/messages/{msg_ids[0]}/events?event_type=agent_start"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["events"][0]["event_type"] == "agent_start"

    async def test_get_events_message_not_found(
        self, client: AsyncClient, conv_with_messages
    ):
        conv_id, _ = conv_with_messages
        resp = await client.get(f"/api/v1/chat/{conv_id}/messages/msg-nonexistent/events")
        assert resp.status_code == 404

    async def test_get_events_cross_user(
        self, admin_client: AsyncClient, conv_with_messages
    ):
        conv_id, msg_ids = conv_with_messages
        resp = await admin_client.get(
            f"/api/v1/chat/{conv_id}/messages/{msg_ids[0]}/events"
        )
        assert resp.status_code == 404

    async def test_get_events_message_wrong_conversation(
        self, client: AsyncClient, db_manager: DatabaseManager, test_user: User
    ):
        """Message exists but belongs to a different conversation → 404."""
        conv1_id, msg1_ids = await _seed_conversation(db_manager, test_user.id)
        conv2_id, msg2_ids = await _seed_conversation(db_manager, test_user.id)

        # Try to get msg from conv1 via conv2's URL
        resp = await client.get(
            f"/api/v1/chat/{conv2_id}/messages/{msg1_ids[0]}/events"
        )
        assert resp.status_code == 404
