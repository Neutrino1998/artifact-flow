"""
ConversationRepository contract tests.

Covers conversation CRUD, message CRUD, count/pagination,
branching paths, and format_conversation_history.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from db.models import User, Conversation, Message
from repositories.conversation_repo import ConversationRepository
from repositories.base import NotFoundError, DuplicateError


# ============================================================
# Local fixtures
# ============================================================


@pytest.fixture
async def sample_conversation(conversation_repo: ConversationRepository, test_user: User):
    """Create a conversation owned by test_user (auto-creates ArtifactSession)."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    conv = await conversation_repo.create_conversation(
        conversation_id=conv_id, title="Sample", user_id=test_user.id
    )
    return conv


@pytest.fixture
async def branched_conversation(conversation_repo: ConversationRepository, test_user: User):
    """
    Create a conversation with a branching message tree:

        root
        ├── msg_a → msg_b  (linear chain, active_branch = msg_b)
        └── msg_c          (branch)
    """
    conv_id = f"conv-{uuid.uuid4().hex}"
    conv = await conversation_repo.create_conversation(
        conversation_id=conv_id, title="Branched", user_id=test_user.id
    )

    root_id = f"msg-root-{uuid.uuid4().hex[:8]}"
    msg_a_id = f"msg-a-{uuid.uuid4().hex[:8]}"
    msg_b_id = f"msg-b-{uuid.uuid4().hex[:8]}"
    msg_c_id = f"msg-c-{uuid.uuid4().hex[:8]}"

    root = await conversation_repo.add_message(
        conv_id, root_id, "root content", "thd-1", parent_id=None
    )
    msg_a = await conversation_repo.add_message(
        conv_id, msg_a_id, "msg_a content", "thd-1", parent_id=root_id
    )
    msg_b = await conversation_repo.add_message(
        conv_id, msg_b_id, "msg_b content", "thd-1", parent_id=msg_a_id
    )
    msg_c = await conversation_repo.add_message(
        conv_id, msg_c_id, "msg_c content", "thd-1", parent_id=root_id
    )

    # Set active_branch to msg_b (linear chain tip)
    await conversation_repo.update_active_branch(conv_id, msg_b_id)

    return {
        "conv_id": conv_id,
        "root_id": root_id,
        "msg_a_id": msg_a_id,
        "msg_b_id": msg_b_id,
        "msg_c_id": msg_c_id,
    }


# ============================================================
# Conversation CRUD
# ============================================================


class TestConversationCRUD:

    async def test_create_conversation_with_artifact_session(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        conv = await conversation_repo.create_conversation(
            conversation_id=conv_id, title="Test", user_id=test_user.id
        )
        assert conv.id == conv_id
        assert conv.artifact_session is not None
        assert conv.artifact_session.id == conv_id

    async def test_create_conversation_duplicate_raises(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        with pytest.raises(DuplicateError):
            await conversation_repo.create_conversation(
                conversation_id=sample_conversation.id
            )

    async def test_get_conversation_not_found(
        self, conversation_repo: ConversationRepository
    ):
        result = await conversation_repo.get_conversation("nonexistent")
        assert result is None

    async def test_get_conversation_or_raise(
        self, conversation_repo: ConversationRepository
    ):
        with pytest.raises(NotFoundError):
            await conversation_repo.get_conversation_or_raise("nonexistent")

    async def test_get_conversation_load_messages(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        conv_id = sample_conversation.id
        # Add a message first
        await conversation_repo.add_message(
            conv_id,
            f"msg-{uuid.uuid4().hex}",
            "hello",
            "thd-x",
        )
        # Expire cached Conversation so selectinload re-fires within same session
        conversation_repo.session.expire(sample_conversation)

        conv = await conversation_repo.get_conversation(conv_id, load_messages=True)
        assert conv is not None
        assert len(conv.messages) >= 1

    async def test_update_title(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        # Pin updated_at to a known past value
        old_time = datetime(2000, 1, 1)
        sample_conversation.updated_at = old_time
        await conversation_repo.update(sample_conversation)

        updated = await conversation_repo.update_title(sample_conversation.id, "New Title")
        assert updated.title == "New Title"
        assert updated.updated_at > old_time

    async def test_update_active_branch(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg = await conversation_repo.add_message(
            sample_conversation.id, f"msg-{uuid.uuid4().hex}", "hi", "thd-x"
        )
        updated = await conversation_repo.update_active_branch(
            sample_conversation.id, msg.id
        )
        assert updated.active_branch == msg.id

    async def test_update_active_branch_nonexistent(
        self, conversation_repo: ConversationRepository
    ):
        with pytest.raises(NotFoundError):
            await conversation_repo.update_active_branch("nonexistent", "msg-x")

    async def test_delete_conversation_cascades(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        conv = await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )
        await conversation_repo.add_message(conv_id, f"msg-{uuid.uuid4().hex}", "hi", "thd-x")

        result = await conversation_repo.delete_conversation(conv_id)
        assert result is True

        # Conversation and messages should be gone
        assert await conversation_repo.get_conversation(conv_id) is None

    async def test_delete_conversation_nonexistent(
        self, conversation_repo: ConversationRepository
    ):
        result = await conversation_repo.delete_conversation("nonexistent")
        assert result is False


# ============================================================
# Count / Pagination
# ============================================================


class TestConversationCountPagination:

    async def test_count_conversations_by_user(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        for _ in range(3):
            await conversation_repo.create_conversation(
                conversation_id=f"conv-{uuid.uuid4().hex}", user_id=test_user.id
            )
        count = await conversation_repo.count_conversations(user_id=test_user.id)
        assert count == 3

    async def test_list_conversations_pagination(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        for _ in range(4):
            await conversation_repo.create_conversation(
                conversation_id=f"conv-{uuid.uuid4().hex}", user_id=test_user.id
            )

        page = await conversation_repo.list_conversations(
            user_id=test_user.id, limit=2, offset=0
        )
        assert len(page) == 2

        page2 = await conversation_repo.list_conversations(
            user_id=test_user.id, limit=2, offset=3
        )
        assert len(page2) == 1

    async def test_list_conversations_ordered_by_updated(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        ids = []
        for i in range(3):
            cid = f"conv-{uuid.uuid4().hex}"
            await conversation_repo.create_conversation(
                conversation_id=cid, user_id=test_user.id
            )
            ids.append(cid)

        # Explicitly set updated_at so ids[0] is the most recent
        conv0 = await conversation_repo.get_conversation_or_raise(ids[0])
        conv0.updated_at = datetime.now() + timedelta(seconds=10)
        await conversation_repo.update(conv0)

        convs = await conversation_repo.list_conversations(
            user_id=test_user.id, order_by_updated=True
        )
        assert convs[0].id == ids[0]


# ============================================================
# Message CRUD
# ============================================================


class TestMessageCRUD:

    async def test_add_message_updates_active_branch(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        msg = await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello", "thd-x"
        )

        conv = await conversation_repo.get_conversation(sample_conversation.id)
        assert conv.active_branch == msg_id

    async def test_add_message_nonexistent_conversation(
        self, conversation_repo: ConversationRepository
    ):
        with pytest.raises(NotFoundError):
            await conversation_repo.add_message(
                "nonexistent", f"msg-{uuid.uuid4().hex}", "hello", "thd-x"
            )

    async def test_add_message_duplicate_raises(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello", "thd-x"
        )
        with pytest.raises(DuplicateError):
            await conversation_repo.add_message(
                sample_conversation.id, msg_id, "duplicate", "thd-x"
            )

    async def test_get_message_and_or_raise(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello", "thd-x"
        )

        # get_message returns the message
        msg = await conversation_repo.get_message(msg_id)
        assert msg is not None
        assert msg.content == "hello"

        # get_message returns None for nonexistent
        assert await conversation_repo.get_message("nonexistent") is None

        # get_message_or_raise raises for nonexistent
        with pytest.raises(NotFoundError):
            await conversation_repo.get_message_or_raise("nonexistent")

    async def test_update_graph_response(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello", "thd-x"
        )

        # Pin updated_at to a known past value
        old_time = datetime(2000, 1, 1)
        conv = await conversation_repo.get_conversation(sample_conversation.id)
        conv.updated_at = old_time
        await conversation_repo.update(conv)

        updated_msg = await conversation_repo.update_graph_response(msg_id, "world")
        assert updated_msg.graph_response == "world"

        conv = await conversation_repo.get_conversation(sample_conversation.id)
        assert conv.updated_at > old_time

    async def test_get_conversation_messages_ordered(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        ids = []
        for i in range(3):
            mid = f"msg-{uuid.uuid4().hex}"
            await conversation_repo.add_message(
                sample_conversation.id, mid, f"msg-{i}", "thd-x"
            )
            ids.append(mid)

        messages = await conversation_repo.get_conversation_messages(sample_conversation.id)
        assert len(messages) == 3
        # Should be ordered by created_at
        for i in range(len(messages) - 1):
            assert messages[i].created_at <= messages[i + 1].created_at


# ============================================================
# Branch Path (PG migration baseline)
# ============================================================


class TestBranchPath:

    async def test_get_conversation_path_linear(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )

        msg1_id = f"msg-{uuid.uuid4().hex}"
        msg2_id = f"msg-{uuid.uuid4().hex}"
        msg3_id = f"msg-{uuid.uuid4().hex}"

        await conversation_repo.add_message(conv_id, msg1_id, "m1", "thd-1")
        await conversation_repo.add_message(conv_id, msg2_id, "m2", "thd-1", parent_id=msg1_id)
        await conversation_repo.add_message(conv_id, msg3_id, "m3", "thd-1", parent_id=msg2_id)

        path = await conversation_repo.get_conversation_path(conv_id, msg3_id)
        assert len(path) == 3
        assert [m.id for m in path] == [msg1_id, msg2_id, msg3_id]

    async def test_get_conversation_path_to_specific_message(
        self, conversation_repo: ConversationRepository, branched_conversation
    ):
        bc = branched_conversation
        path = await conversation_repo.get_conversation_path(bc["conv_id"], bc["msg_c_id"])
        assert len(path) == 2
        assert [m.id for m in path] == [bc["root_id"], bc["msg_c_id"]]

    async def test_get_conversation_path_uses_active_branch(
        self, conversation_repo: ConversationRepository, branched_conversation
    ):
        bc = branched_conversation
        # active_branch is msg_b → path should be root → msg_a → msg_b
        path = await conversation_repo.get_conversation_path(bc["conv_id"])
        assert len(path) == 3
        assert [m.id for m in path] == [bc["root_id"], bc["msg_a_id"], bc["msg_b_id"]]

    async def test_get_conversation_path_empty(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )
        path = await conversation_repo.get_conversation_path(conv_id)
        assert path == []

    async def test_get_branch_children(
        self, conversation_repo: ConversationRepository, branched_conversation
    ):
        bc = branched_conversation
        children = await conversation_repo.get_branch_children(bc["conv_id"], bc["root_id"])
        child_ids = [c.id for c in children]
        assert len(child_ids) == 2
        assert bc["msg_a_id"] in child_ids
        assert bc["msg_c_id"] in child_ids

    async def test_get_branch_structure(
        self, conversation_repo: ConversationRepository, branched_conversation
    ):
        bc = branched_conversation
        structure = await conversation_repo.get_branch_structure(bc["conv_id"])

        # root has two children
        assert bc["root_id"] in structure
        assert len(structure[bc["root_id"]]) == 2

        # msg_a has one child (msg_b)
        assert bc["msg_a_id"] in structure
        assert structure[bc["msg_a_id"]] == [bc["msg_b_id"]]

    async def test_format_conversation_history(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )

        msg1_id = f"msg-{uuid.uuid4().hex}"
        msg2_id = f"msg-{uuid.uuid4().hex}"

        await conversation_repo.add_message(conv_id, msg1_id, "hello", "thd-1")
        await conversation_repo.update_graph_response(msg1_id, "hi there")

        await conversation_repo.add_message(
            conv_id, msg2_id, "how are you", "thd-1", parent_id=msg1_id
        )
        await conversation_repo.update_graph_response(msg2_id, "doing great")

        history = await conversation_repo.format_conversation_history(conv_id, msg2_id)
        assert len(history) == 4  # 2 user + 2 assistant
        assert history[0] == {"role": "user", "content": "hello"}
        assert history[1] == {"role": "assistant", "content": "hi there"}
        assert history[2] == {"role": "user", "content": "how are you"}
        assert history[3] == {"role": "assistant", "content": "doing great"}

    async def test_format_history_skips_no_response(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )

        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(conv_id, msg_id, "hello", "thd-1")
        # No graph_response set

        history = await conversation_repo.format_conversation_history(conv_id, msg_id)
        assert len(history) == 1  # only user turn
        assert history[0] == {"role": "user", "content": "hello"}
