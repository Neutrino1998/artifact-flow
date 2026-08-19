"""
ConversationRepository contract tests.

Covers conversation CRUD, message CRUD, count/pagination,
branching paths, and format_conversation_history.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from core.management.conversation_manager import ConversationManager
from db.models import User, Conversation, Message
from db.database import DatabaseManager
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
        conv_id, root_id, "root content", parent_id=None
    )
    msg_a = await conversation_repo.add_message(
        conv_id, msg_a_id, "msg_a content", parent_id=root_id
    )
    msg_b = await conversation_repo.add_message(
        conv_id, msg_b_id, "msg_b content", parent_id=msg_a_id
    )
    msg_c = await conversation_repo.add_message(
        conv_id, msg_c_id, "msg_c content", parent_id=root_id
    )

    # active_branch is automatically set to msg_c (last add_message call)
    # but we need it to be msg_b for the linear chain tests
    conv_obj = await conversation_repo.get_conversation(conv_id)
    conv_obj.active_branch = msg_b_id
    conv_obj.updated_at = datetime.now()
    await conversation_repo.update(conv_obj)

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

    async def test_delete_conversation_cascades(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        conv = await conversation_repo.create_conversation(
            conversation_id=conv_id, user_id=test_user.id
        )
        await conversation_repo.add_message(conv_id, f"msg-{uuid.uuid4().hex}", "hi")

        result = await conversation_repo.delete_conversation(conv_id)
        assert result is True

        # Conversation and messages should be gone
        assert await conversation_repo.get_conversation(conv_id) is None

    async def test_delete_conversation_nonexistent(
        self, conversation_repo: ConversationRepository
    ):
        result = await conversation_repo.delete_conversation("nonexistent")
        assert result is False

    async def test_exists_returns_true_for_existing(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        assert await conversation_repo.exists(sample_conversation.id) is True

    async def test_exists_returns_false_for_missing(
        self, conversation_repo: ConversationRepository
    ):
        assert await conversation_repo.exists("conv-does-not-exist") is False

    async def test_exists_after_delete(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(conversation_id=conv_id, user_id=test_user.id)
        assert await conversation_repo.exists(conv_id) is True

        await conversation_repo.delete_conversation(conv_id)
        assert await conversation_repo.exists(conv_id) is False


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
            sample_conversation.id, msg_id, "hello"
        )

        conv = await conversation_repo.get_conversation(sample_conversation.id)
        assert conv.active_branch == msg_id

    async def test_add_message_nonexistent_conversation(
        self, conversation_repo: ConversationRepository
    ):
        with pytest.raises(NotFoundError):
            await conversation_repo.add_message(
                "nonexistent", f"msg-{uuid.uuid4().hex}", "hello"
            )

    async def test_add_message_duplicate_raises(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello"
        )
        with pytest.raises(DuplicateError):
            await conversation_repo.add_message(
                sample_conversation.id, msg_id, "duplicate"
            )

    async def test_add_message_rejects_missing_parent(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        with pytest.raises(NotFoundError):
            await conversation_repo.add_message(
                sample_conversation.id,
                f"msg-{uuid.uuid4().hex}",
                "child",
                parent_id="msg-does-not-exist",
            )

    async def test_add_message_rejects_parent_from_other_conversation(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        conv_a = f"conv-{uuid.uuid4().hex}"
        conv_b = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(conv_a, user_id=test_user.id)
        await conversation_repo.create_conversation(conv_b, user_id=test_user.id)

        parent_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(conv_a, parent_id, "parent")

        with pytest.raises(NotFoundError):
            await conversation_repo.add_message(
                conv_b,
                f"msg-{uuid.uuid4().hex}",
                "child",
                parent_id=parent_id,
            )

    async def test_get_message_and_or_raise(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello"
        )

        # get_message returns the message
        msg = await conversation_repo.get_message(msg_id)
        assert msg is not None
        assert msg.user_input == "hello"

        # get_message returns None for nonexistent
        assert await conversation_repo.get_message("nonexistent") is None

        # get_message_or_raise raises for nonexistent
        with pytest.raises(NotFoundError):
            await conversation_repo.get_message_or_raise("nonexistent")

    async def test_update_response(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        msg_id = f"msg-{uuid.uuid4().hex}"
        await conversation_repo.add_message(
            sample_conversation.id, msg_id, "hello"
        )

        # Pin updated_at to a known past value
        old_time = datetime(2000, 1, 1)
        conv = await conversation_repo.get_conversation(sample_conversation.id)
        conv.updated_at = old_time
        await conversation_repo.update(conv)

        updated_msg = await conversation_repo.update_response(msg_id, "world")
        assert updated_msg.response == "world"

        conv = await conversation_repo.get_conversation(sample_conversation.id)
        assert conv.updated_at > old_time

    async def test_update_metadata_merges_over_nonempty_json_and_persists(
        self,
        conversation_repo: ConversationRepository,
        sample_conversation: Conversation,
        db_manager,
    ):
        """A display snapshot written at message creation must not prevent the
        later terminal metadata merge from being detected by SQLAlchemy."""
        msg_id = f"msg-{uuid.uuid4().hex}"
        activated = [{"slug": "docx", "name": "Word documents"}]
        await conversation_repo.add_message(
            sample_conversation.id,
            msg_id,
            "hello",
            metadata={"activated_skills": activated},
        )

        await conversation_repo.update_message_metadata(
            msg_id,
            {"execution_metrics": {"total_duration_ms": 123}},
        )

        # A fresh session distinguishes a real committed write from an identity-map
        # object that was only mutated in memory.
        async with db_manager.session() as session:
            persisted = await ConversationRepository(session).get_message(msg_id)
            assert persisted.metadata_ == {
                "activated_skills": activated,
                "execution_metrics": {"total_duration_ms": 123},
            }

    async def test_get_conversation_messages_ordered(
        self, conversation_repo: ConversationRepository, sample_conversation: Conversation
    ):
        ids = []
        for i in range(3):
            mid = f"msg-{uuid.uuid4().hex}"
            await conversation_repo.add_message(
                sample_conversation.id, mid, f"msg-{i}"
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

        await conversation_repo.add_message(conv_id, msg1_id, "m1")
        await conversation_repo.add_message(conv_id, msg2_id, "m2", parent_id=msg1_id)
        await conversation_repo.add_message(conv_id, msg3_id, "m3", parent_id=msg2_id)

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


class TestRetryIdempotency:
    """ConversationManager 的 setup 写被 turn handler 的 retry 边界包裹;with_retry 在瞬断后
    从头重跑 fn → 这些写必须幂等（同 id 第二遍不得抛）。"""

    async def test_append_message_idempotent_on_retry(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        # 模拟 with_retry:首次已 commit message 后瞬断 → 重跑 append_message。
        # 修复前第二遍撞 DuplicateError(非瞬断)逃出 with_retry → 整轮崩;修复后撞重当成功。
        from core.management.conversation_manager import ConversationManager
        mgr = ConversationManager(conversation_repo)
        conv_id = f"conv-{uuid.uuid4().hex}"
        msg_id = f"msg-{uuid.uuid4().hex}"
        await mgr.create(conv_id, user_id=test_user.id)

        activated = [{"slug": "docx", "name": "Word documents"}]
        await mgr.append_message(
            conv_id=conv_id,
            message_id=msg_id,
            user_input="hi",
            metadata={"activated_skills": activated},
        )
        # 第二遍(= retry 从头重跑)不得抛
        await mgr.append_message(
            conv_id=conv_id,
            message_id=msg_id,
            user_input="hi",
            metadata={"activated_skills": activated},
        )

        message = await conversation_repo.get_message(msg_id)
        assert message is not None
        assert message.metadata_["activated_skills"] == activated

    async def test_append_message_blank_root_title_falls_back(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        from core.management.conversation_manager import ConversationManager
        mgr = ConversationManager(conversation_repo)
        conv_id = f"conv-{uuid.uuid4().hex}"
        msg_id = f"msg-{uuid.uuid4().hex}"
        await mgr.create(conv_id, user_id=test_user.id)

        await mgr.append_message(
            conv_id=conv_id,
            message_id=msg_id,
            user_input="",
            parent_id=None,
        )

        conv = await conversation_repo.get_conversation(conv_id)
        assert conv is not None
        assert conv.title == "Untitled"

    async def test_append_message_requires_existing_conversation(
        self, conversation_repo: ConversationRepository
    ):
        """Append fails closed instead of resurrecting a deleted parent row."""
        from core.management.conversation_manager import ConversationManager

        mgr = ConversationManager(conversation_repo)
        conv_id = f"conv-{uuid.uuid4().hex}"

        with pytest.raises(NotFoundError):
            await mgr.append_message(
                conv_id=conv_id,
                message_id=f"msg-{uuid.uuid4().hex}",
                user_input="hi",
            )

        assert await conversation_repo.get_conversation(conv_id) is None

    async def test_create_idempotent_on_same_stable_id(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        # #2 的修法(turn handler 在 retry 边界外定 conv_id 再传入)依赖此幂等:固定 id
        # 重复调用返回同 id、不抛(撞重被 manager 吞)。
        from core.management.conversation_manager import ConversationManager
        mgr = ConversationManager(conversation_repo)
        conv_id = f"conv-{uuid.uuid4().hex}"

        assert await mgr.create(conv_id, user_id=test_user.id) == conv_id
        assert await mgr.create(conv_id, user_id=test_user.id) == conv_id

    async def test_require_owned_hides_missing_and_wrong_owner(
        self, conversation_repo: ConversationRepository, test_user: User
    ):
        from core.management.conversation_manager import ConversationManager

        mgr = ConversationManager(conversation_repo)
        conv_id = f"conv-{uuid.uuid4().hex}"
        await mgr.create(conv_id, user_id=test_user.id)

        assert await mgr.require_owned(conv_id, test_user.id) is None

        with pytest.raises(NotFoundError):
            await mgr.require_owned(conv_id, "different-user")
        with pytest.raises(NotFoundError):
            await mgr.require_owned(f"conv-{uuid.uuid4().hex}", test_user.id)

    async def test_manager_requires_repository_at_construction(self):
        """Manager has no dependencyless/test-only persistence mode."""
        from core.management.conversation_manager import ConversationManager

        with pytest.raises(ValueError, match="requires a repository"):
            ConversationManager(None)

    async def test_fixed_conversation_id_survives_post_commit_disconnect(
        self,
        db_manager: DatabaseManager,
        test_user: User,
        monkeypatch,
    ):
        """A retry after commit reuses the caller-allocated conversation ID.

        Raising ``OperationalError`` after the repository returns models the
        reachable "commit succeeded, acknowledgement/post-commit work failed"
        shape.  ``DatabaseManager.with_retry`` opens a fresh session and reruns
        the whole operation; the duplicate fixed ID is idempotent success.
        """
        conv_id = f"conv-{uuid.uuid4().hex}"
        original_create = ConversationRepository.create_conversation
        disconnected = False
        attempts = 0

        async def create_then_disconnect(repo, *args, **kwargs):
            nonlocal disconnected
            result = await original_create(repo, *args, **kwargs)
            if kwargs.get("conversation_id") == conv_id and not disconnected:
                disconnected = True
                raise OperationalError(
                    "post-commit acknowledgement",
                    {},
                    ConnectionError("connection lost after commit"),
                )
            return result

        monkeypatch.setattr(
            ConversationRepository,
            "create_conversation",
            create_then_disconnect,
        )

        async def create_with_fresh_session(session):
            nonlocal attempts
            attempts += 1
            manager = ConversationManager(ConversationRepository(session))
            return await manager.create(
                conv_id,
                user_id=test_user.id,
            )

        result = await db_manager.with_retry(
            create_with_fresh_session,
            max_retries=1,
            base_delay=0,
        )

        assert result == conv_id
        assert attempts == 2
        async with db_manager.session() as session:
            persisted = await ConversationRepository(session).get_conversation(conv_id)
            assert persisted is not None
            assert persisted.user_id == test_user.id

    async def test_message_retry_after_insert_commit_still_finishes_root_title(
        self,
        db_manager: DatabaseManager,
        test_user: User,
        monkeypatch,
    ):
        """Retry must continue past Duplicate and finish the later title write.

        ``add_message`` commits Message + active_branch before ``update_title``.
        A disconnect at that boundary reruns the whole manager operation: the
        stable message ID collides, is treated as success, and title update must
        still execute instead of returning early from the Duplicate branch.
        """
        conv_id = f"conv-{uuid.uuid4().hex}"
        msg_id = f"msg-{uuid.uuid4().hex}"
        async with db_manager.session() as session:
            await ConversationRepository(session).create_conversation(
                conversation_id=conv_id,
                user_id=test_user.id,
            )

        original_update_title = ConversationRepository.update_title
        disconnected = False
        attempts = 0

        async def disconnect_before_first_title(repo, conversation_id, title):
            nonlocal disconnected
            if conversation_id == conv_id and not disconnected:
                disconnected = True
                raise OperationalError(
                    "title update after committed message",
                    {},
                    ConnectionError("connection lost before title update"),
                )
            return await original_update_title(repo, conversation_id, title)

        monkeypatch.setattr(
            ConversationRepository,
            "update_title",
            disconnect_before_first_title,
        )

        async def append_with_fresh_session(session):
            nonlocal attempts
            attempts += 1
            manager = ConversationManager(ConversationRepository(session))
            return await manager.append_message(
                conv_id=conv_id,
                message_id=msg_id,
                user_input="Stable retry title",
                parent_id=None,
            )

        await db_manager.with_retry(
            append_with_fresh_session,
            max_retries=1,
            base_delay=0,
        )

        assert attempts == 2
        async with db_manager.session() as session:
            repo = ConversationRepository(session)
            persisted = await repo.get_message(msg_id)
            conversation = await repo.get_conversation(conv_id)
            assert persisted is not None
            assert persisted.parent_id is None
            assert conversation is not None
            assert conversation.active_branch == msg_id
            assert conversation.title == "Stable retry title"
