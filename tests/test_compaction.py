"""
CompactionManager unit tests.

Mock strategy: patch("models.llm.astream_with_retry") + real DB (db_manager fixture).

Note: TestTrigger/TestWait use a lightweight CompactionManager without DB interaction.
TestCompactLogic uses db_manager directly (not via db_session) to avoid event loop scope issues.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from unittest.mock import patch, MagicMock

import pytest

from core.compaction import CompactionManager, _PairInfo
from db.database import DatabaseManager
from db.models import User
from repositories.conversation_repo import ConversationRepository
from repositories.user_repo import UserRepository
from api.services.auth import hash_password


# ============================================================
# Helpers
# ============================================================


@dataclass
class _FakeAgentConfig:
    name: str = "compact_agent"
    description: str = "Compaction agent"
    tools: dict = field(default_factory=dict)
    model: str = "fake-model"
    max_tool_rounds: int = 1
    role_prompt: str = "Summarize the conversation."
    internal: bool = True


@dataclass
class _FakeCompactionConfig:
    COMPACTION_THRESHOLD: int = 1000
    COMPACTION_PRESERVE_PAIRS: int = 2
    COMPACTION_TIMEOUT: int = 10


def _make_fake_stream(text: str):
    """Fake LLM stream that returns a single text response."""
    async def fake(messages, **kwargs):
        yield {"type": "content", "content": text}
        yield {"type": "final", "content": text, "reasoning_content": None, "token_usage": {}}
    return fake


async def _seed_conversation(
    db_manager: DatabaseManager,
    user_id: str,
    n_pairs: int = 5,
) -> tuple[str, list[str]]:
    """Seed a conversation with n message pairs. Returns (conv_id, [msg_ids])."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    msg_ids = []

    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        await repo.create_conversation(conv_id, user_id=user_id)

        parent_id = None
        for i in range(n_pairs):
            msg_id = f"msg-{uuid.uuid4().hex}"
            msg_ids.append(msg_id)
            await repo.add_message(conv_id, msg_id, f"user input {i}", parent_id=parent_id)
            await repo.update_response(msg_id, f"response {i}")
            parent_id = msg_id

    return conv_id, msg_ids


async def _create_user(db_manager: DatabaseManager) -> User:
    """Create a test user directly via db_manager (no db_session fixture needed)."""
    user_id = str(uuid.uuid4())
    async with db_manager.session() as session:
        repo = UserRepository(session)
        user = User(
            id=user_id,
            username=f"compact_user_{user_id[:8]}",
            hashed_password=hash_password("testpass"),
            role="user",
            is_active=True,
        )
        return await repo.add(user)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def agents():
    return {"compact_agent": _FakeAgentConfig()}


@pytest.fixture
def config():
    return _FakeCompactionConfig()


# Lightweight CM for trigger/wait tests (no real DB needed)
@pytest.fixture
def cm_light(agents):
    """CompactionManager with a dummy db_manager for tests that don't touch DB."""
    return CompactionManager(db_manager=MagicMock(), agents=agents)


# ============================================================
# TestTrigger
# ============================================================


class TestTrigger:

    async def test_below_threshold_no_trigger(self, cm_light, config):
        metrics = {"last_context_chars": 500}
        await cm_light.maybe_trigger("conv-1", "msg-1", metrics, config)
        assert "conv-1" not in cm_light._running

    async def test_above_threshold_triggers(self, cm_light, config):
        metrics = {"last_context_chars": 2000}
        with patch.object(cm_light, "_compact", return_value=None) as mock_compact:
            await cm_light.maybe_trigger("conv-1", "msg-1", metrics, config)
            # Wait for the background task to finish so the patch stays alive
            event = cm_light._running.get("conv-1")
            if event:
                await event.wait()
            mock_compact.assert_awaited_once()
        # _running should be cleaned up after completion
        assert "conv-1" not in cm_light._running

    async def test_already_running_skips(self, cm_light, config):
        cm_light._running["conv-1"] = asyncio.Event()
        metrics = {"last_context_chars": 2000}
        await cm_light.maybe_trigger("conv-1", "msg-1", metrics, config)
        assert "conv-1" in cm_light._running

    async def test_manual_trigger_success(self, cm_light, config):
        with patch.object(cm_light, "_compact", return_value=None) as mock_compact:
            result = await cm_light.trigger("conv-1", config)
            assert result is True
            # Wait for the background task to finish within the patch scope
            event = cm_light._running.get("conv-1")
            if event:
                await event.wait()
            mock_compact.assert_awaited_once()
        assert "conv-1" not in cm_light._running

    async def test_manual_trigger_already_running(self, cm_light, config):
        cm_light._running["conv-1"] = asyncio.Event()
        result = await cm_light.trigger("conv-1", config)
        assert result is False


# ============================================================
# TestWait
# ============================================================


class TestWait:

    async def test_wait_running_blocks(self, cm_light):
        done_event = asyncio.Event()
        cm_light._running["conv-1"] = done_event

        waited = asyncio.Event()

        async def waiter():
            result = await cm_light.wait_if_running("conv-1")
            waited.set()
            return result

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert not waited.is_set()

        done_event.set()
        result = await task
        assert result is True

    async def test_wait_not_running(self, cm_light):
        result = await cm_light.wait_if_running("conv-1")
        assert result is False


# ============================================================
# TestCompactLogic (uses real DB)
# ============================================================


class TestCompactLogic:

    async def test_compact_writes_summaries(self, db_manager, agents, config):
        cm = CompactionManager(db_manager=db_manager, agents=agents)
        user = await _create_user(db_manager)
        conv_id, msg_ids = await _seed_conversation(db_manager, user.id, n_pairs=5)

        summary_xml = (
            "<user_input_summary>User asked about X</user_input_summary>\n"
            "<response_summary>Assistant explained X</response_summary>"
        )

        with patch("models.llm.astream_with_retry", _make_fake_stream(summary_xml)):
            await cm._compact(conv_id, msg_ids[-1], config)

        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            for msg_id in msg_ids[:3]:  # 5 - 2 preserved = 3 compacted
                msg = await repo.get_message(msg_id)
                assert msg.user_input_summary is not None
                assert msg.response_summary is not None

    async def test_existing_summary_skipped(self, db_manager, agents, config):
        cm = CompactionManager(db_manager=db_manager, agents=agents)
        user = await _create_user(db_manager)
        conv_id, msg_ids = await _seed_conversation(db_manager, user.id, n_pairs=5)

        # Pre-write summary for first message
        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            msg = await repo.get_message(msg_ids[0])
            msg.user_input_summary = "existing user summary"
            msg.response_summary = "existing response summary"
            await session.flush()
            await session.commit()

        call_count = {"n": 0}

        async def counting_stream(messages, **kwargs):
            call_count["n"] += 1
            text = (
                "<user_input_summary>new summary</user_input_summary>\n"
                "<response_summary>new response</response_summary>"
            )
            yield {"type": "content", "content": text}
            yield {"type": "final", "content": text, "reasoning_content": None, "token_usage": {}}

        with patch("models.llm.astream_with_retry", counting_stream):
            await cm._compact(conv_id, msg_ids[-1], config)

        # 3 pairs to compact - 1 existing = 2 LLM calls
        assert call_count["n"] == 2

    async def test_preserve_recent_pairs(self, db_manager, agents, config):
        cm = CompactionManager(db_manager=db_manager, agents=agents)
        user = await _create_user(db_manager)
        config.COMPACTION_PRESERVE_PAIRS = 2
        conv_id, msg_ids = await _seed_conversation(db_manager, user.id, n_pairs=4)

        summary_xml = (
            "<user_input_summary>summary</user_input_summary>\n"
            "<response_summary>summary</response_summary>"
        )

        with patch("models.llm.astream_with_retry", _make_fake_stream(summary_xml)):
            await cm._compact(conv_id, msg_ids[-1], config)

        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            for msg_id in msg_ids[-2:]:
                msg = await repo.get_message(msg_id)
                assert msg.user_input_summary is None

    async def test_llm_failure_continues(self, db_manager, agents, config):
        cm = CompactionManager(db_manager=db_manager, agents=agents)
        user = await _create_user(db_manager)
        conv_id, msg_ids = await _seed_conversation(db_manager, user.id, n_pairs=5)
        call_count = {"n": 0}

        async def failing_then_success(messages, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("LLM error")
            text = (
                "<user_input_summary>ok summary</user_input_summary>\n"
                "<response_summary>ok response</response_summary>"
            )
            yield {"type": "content", "content": text}
            yield {"type": "final", "content": text, "reasoning_content": None, "token_usage": {}}

        with patch("models.llm.astream_with_retry", failing_then_success):
            await cm._compact(conv_id, msg_ids[-1], config)

        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            msg0 = await repo.get_message(msg_ids[0])
            assert msg0.user_input_summary is None  # LLM failed

            msg1 = await repo.get_message(msg_ids[1])
            assert msg1.user_input_summary is not None

    async def test_xml_parse_failure_skips(self, db_manager, agents, config):
        cm = CompactionManager(db_manager=db_manager, agents=agents)
        user = await _create_user(db_manager)
        conv_id, msg_ids = await _seed_conversation(db_manager, user.id, n_pairs=4)

        with patch("models.llm.astream_with_retry", _make_fake_stream("no xml tags here")):
            await cm._compact(conv_id, msg_ids[-1], config)

        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            for msg_id in msg_ids[:2]:
                msg = await repo.get_message(msg_id)
                assert msg.user_input_summary is None


# ============================================================
# TestExtractTag
# ============================================================


class TestExtractTag:

    def test_normal_extraction(self):
        text = "<user_input_summary>Hello world</user_input_summary>"
        result = CompactionManager._extract_tag(text, "user_input_summary")
        assert result == "Hello world"

    def test_missing_tag_returns_none(self):
        text = "no tags here"
        result = CompactionManager._extract_tag(text, "user_input_summary")
        assert result is None

    def test_multiline_extraction(self):
        text = "<response_summary>\nLine 1\nLine 2\n</response_summary>"
        result = CompactionManager._extract_tag(text, "response_summary")
        assert "Line 1" in result
        assert "Line 2" in result
