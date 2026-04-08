"""
Chat endpoint integration tests for inject, cancel, compact, events APIs,
and the main POST /chat → GET /stream end-to-end flow.

Uses API fixtures with simulated active tasks.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Tuple, List
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from db.models import User
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from api.services.execution_runner import ExecutionRunner
from api.dependencies import get_execution_runner
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


class _MockStreamTransport:
    """Minimal mock for StreamTransport — satisfies submit() orchestration."""
    async def create_stream(self, stream_id, owner_user_id=None, lease_check_key=None, lease_expected_owner=None): pass
    async def close_stream(self, stream_id): return True


async def _simulate_active_task(app, conv_id: str, msg_id: str) -> asyncio.Event:
    """
    Submit a sleeping coroutine to simulate an active task.
    submit() now handles lease + interactive internally.
    Returns a blocker event that can be set to let the task complete.
    """
    runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()

    blocker = asyncio.Event()

    def sleeping_factory():
        async def sleeping():
            await blocker.wait()
        return sleeping()

    await runner.submit(conv_id, msg_id, sleeping_factory, user_id="test-user", stream_transport=_MockStreamTransport())
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

        # Verify the message was actually enqueued in RuntimeStore
        runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
        drained = await runner.store.drain_messages(msg_ids[0])
        assert drained == ["additional input"]

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
        runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()
        assert await runner.store.is_cancelled(msg_ids[0]) is True

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
            async def trigger(self, conv_id):
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
            async def trigger(self, conv_id):
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
            async def trigger(self, conv_id):
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


# ============================================================
# TestChatStreamE2E — POST /chat → GET /stream full flow
# ============================================================


@dataclass
class _FakeAgentConfig:
    name: str = "lead_agent"
    description: str = "test lead"
    tools: dict = field(default_factory=dict)
    model: str = "fake-model"
    max_tool_rounds: int = 3
    role_prompt: str = "You are a test agent."
    internal: bool = False


def _make_fake_llm_stream(text: str):
    """Fake LLM that returns a simple text response."""
    async def fake(messages, **kwargs):
        yield {"type": "content", "content": text}
        yield {"type": "usage", "token_usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }}
        yield {"type": "final", "content": text, "reasoning_content": None, "token_usage": {
            "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        }}
    return fake


def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE text into a list of event dicts."""
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        data_line = None
        for line in block.split("\n"):
            if line.startswith("data: "):
                data_line = line[len("data: "):]
        if data_line:
            try:
                events.append(json.loads(data_line))
            except json.JSONDecodeError:
                pass
    return events


class TestChatStreamE2E:
    """
    End-to-end: POST /chat → background execution → GET /stream → SSE events.

    Mock only the LLM; exercises real ExecutionController, ExecutionRunner,
    InMemoryStreamTransport, conversation persistence, and event persistence.
    """

    async def test_chat_and_stream_happy_path(
        self, client: AsyncClient, app, db_manager: DatabaseManager
    ):
        """POST /chat returns stream_url; GET /stream yields metadata → ... → complete."""
        fake_agents = {"lead_agent": _FakeAgentConfig()}
        runner: ExecutionRunner = app.dependency_overrides[get_execution_runner]()

        # _create_controller() calls get_execution_runner/get_agents/get_tools directly,
        # so we must set the module-level globals (not just dependency_overrides).
        old_agents = deps._agents
        old_tools = deps._tools
        old_runner = deps._execution_runner
        deps._agents = fake_agents
        deps._tools = {}
        deps._execution_runner = runner

        try:
            with patch("models.llm.astream_with_retry", _make_fake_llm_stream("Hello from agent")):
                # 1. POST /chat — starts background execution
                resp = await client.post(
                    "/api/v1/chat",
                    json={"user_input": "Hi there"},
                )
                assert resp.status_code == 200
                body = resp.json()
                conv_id = body["conversation_id"]
                message_id = body["message_id"]
                stream_url = body["stream_url"]
                assert stream_url == f"/api/v1/stream/{message_id}"

                # Wait for background execution to complete
                for _ in range(50):
                    if message_id not in runner._tasks:
                        break
                    await asyncio.sleep(0.1)

                # 2. GET /stream — consume SSE events
                sse_resp = await client.get(stream_url)
                assert sse_resp.status_code == 200

                events = _parse_sse_events(sse_resp.text)
                event_types = [e.get("type") for e in events]

                # Happy path: must have metadata → complete (not error)
                assert "metadata" in event_types
                assert "complete" in event_types, \
                    f"Expected 'complete' terminal event, got: {event_types}"
                assert "error" not in event_types, \
                    f"Unexpected error event in happy path: {event_types}"

                # 3. Verify conversation and response were persisted
                async with db_manager.session() as session:
                    repo = ConversationRepository(session)
                    conv = await repo.get_conversation(conv_id)
                    assert conv is not None

                    msg = await repo.get_message(message_id)
                    assert msg is not None
                    assert msg.user_input == "Hi there"
                    assert msg.response is not None
                    assert "Hello from agent" in msg.response

                # 4. Verify events were persisted
                from repositories.message_event_repo import MessageEventRepository
                async with db_manager.session() as session:
                    event_repo = MessageEventRepository(session)
                    db_events = await event_repo.get_by_message(message_id)
                    db_event_types = [e.event_type for e in db_events]
                    # user_input + agent_start + llm_complete + agent_complete + complete
                    assert len(db_events) >= 4, f"Expected ≥4 persisted events, got: {db_event_types}"
                    assert "complete" in db_event_types

                # 5. Verify lease was cleaned up
                assert await runner.store.get_leased_message_id(conv_id) is None
                assert await runner.store.get_interactive_message_id(conv_id) is None
        finally:
            deps._agents = old_agents
            deps._tools = old_tools
            deps._execution_runner = old_runner
