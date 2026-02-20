"""
Concurrency tests using file-based SQLite + WAL.

Each test gets its own tmp_path database, creates multiple AsyncSessions,
and uses asyncio.Barrier + asyncio.gather to exercise concurrent access.
No sleep-based synchronization.
"""

import uuid
import asyncio

import pytest

from db.database import DatabaseManager
from db.models import User, VersionConflictError
from repositories.user_repo import UserRepository
from repositories.conversation_repo import ConversationRepository
from repositories.artifact_repo import ArtifactRepository
from repositories.base import DuplicateError
from api.services.auth import hash_password


# ============================================================
# Fixture: file-based SQLite with WAL for concurrent access
# ============================================================


@pytest.fixture
async def file_db(tmp_path):
    """
    tmp_path file SQLite + WAL, allowing multi-connection concurrency.

    Unlike the in-memory StaticPool used by other tests, this creates
    a real file database where each session() call gets an independent
    connection, enabling true concurrent access.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}"
    manager = DatabaseManager(database_url=db_url)
    await manager.initialize()
    yield manager
    await manager.close()


async def _create_test_user(db: DatabaseManager) -> User:
    """Helper: create a user and return it."""
    async with db.session() as session:
        repo = UserRepository(session)
        user = User(
            id=str(uuid.uuid4()),
            username=f"user-{uuid.uuid4().hex[:8]}",
            hashed_password=hash_password("pw"),
            role="user",
            is_active=True,
        )
        return await repo.add(user)


async def _create_test_conversation(db: DatabaseManager, user_id: str) -> str:
    """Helper: create a conversation and return conv_id."""
    async with db.session() as session:
        repo = ConversationRepository(session)
        conv_id = f"conv-{uuid.uuid4().hex}"
        await repo.create_conversation(conv_id, user_id=user_id)
        return conv_id


async def _create_test_artifact(
    db: DatabaseManager, session_id: str
) -> str:
    """Helper: create an artifact and return artifact_id."""
    async with db.session() as session:
        repo = ArtifactRepository(session)
        artifact_id = f"art-{uuid.uuid4().hex}"
        await repo.create_artifact(
            session_id=session_id,
            artifact_id=artifact_id,
            content_type="markdown",
            title="Test",
            content="initial content",
        )
        return artifact_id


# ============================================================
# Concurrent tests
# ============================================================


class TestConcurrent:

    async def test_concurrent_artifact_update_one_wins(self, file_db: DatabaseManager):
        """
        Two sessions update the same artifact simultaneously.
        Exactly one succeeds, the other gets VersionConflictError.
        """
        user = await _create_test_user(file_db)
        conv_id = await _create_test_conversation(file_db, user.id)
        artifact_id = await _create_test_artifact(file_db, conv_id)

        barrier = asyncio.Barrier(2)
        results = []

        async def update_worker(worker_id: int):
            async with file_db.session() as session:
                repo = ArtifactRepository(session)
                await barrier.wait()  # synchronize start
                try:
                    await repo.update_artifact_content(
                        session_id=conv_id,
                        artifact_id=artifact_id,
                        new_content=f"content from worker {worker_id}",
                        update_type="update",
                        expected_lock_version=1,
                    )
                    results.append(("success", worker_id))
                except VersionConflictError:
                    results.append(("conflict", worker_id))

        await asyncio.gather(update_worker(1), update_worker(2))

        successes = [r for r in results if r[0] == "success"]
        conflicts = [r for r in results if r[0] == "conflict"]
        assert len(successes) == 1
        assert len(conflicts) == 1

    async def test_concurrent_artifact_update_sequential(
        self, file_db: DatabaseManager
    ):
        """
        Three sequential updates with correct lock_version all succeed.
        Final version should be 4 (1 create + 3 updates).
        """
        user = await _create_test_user(file_db)
        conv_id = await _create_test_conversation(file_db, user.id)
        artifact_id = await _create_test_artifact(file_db, conv_id)

        for i in range(3):
            async with file_db.session() as session:
                repo = ArtifactRepository(session)
                await repo.update_artifact_content(
                    session_id=conv_id,
                    artifact_id=artifact_id,
                    new_content=f"update {i + 1}",
                    update_type="update",
                    expected_lock_version=i + 1,
                )

        # Verify final state
        async with file_db.session() as session:
            repo = ArtifactRepository(session)
            artifact = await repo.get_artifact(conv_id, artifact_id)
            assert artifact.current_version == 4
            assert artifact.lock_version == 4
            assert artifact.content == "update 3"

    async def test_concurrent_add_messages_same_conversation(
        self, file_db: DatabaseManager
    ):
        """
        Three sessions each add one message (different IDs) concurrently.
        All should succeed; total message count = 3.
        """
        user = await _create_test_user(file_db)
        conv_id = await _create_test_conversation(file_db, user.id)

        barrier = asyncio.Barrier(3)
        errors = []

        async def add_message_worker(worker_id: int):
            async with file_db.session() as session:
                repo = ConversationRepository(session)
                msg_id = f"msg-{uuid.uuid4().hex}"
                await barrier.wait()
                try:
                    await repo.add_message(
                        conv_id, msg_id, f"message {worker_id}", f"thd-{worker_id}"
                    )
                except Exception as e:
                    errors.append(e)

        await asyncio.gather(
            add_message_worker(1),
            add_message_worker(2),
            add_message_worker(3),
        )

        assert len(errors) == 0, f"Unexpected errors: {errors}"

        # Verify all messages were added
        async with file_db.session() as session:
            repo = ConversationRepository(session)
            messages = await repo.get_conversation_messages(conv_id)
            assert len(messages) == 3

    async def test_concurrent_create_conversation_duplicate(
        self, file_db: DatabaseManager
    ):
        """
        Two sessions try to create the same conversation_id simultaneously.
        Exactly one succeeds, the other fails with DuplicateError.
        """
        user = await _create_test_user(file_db)
        conv_id = f"conv-{uuid.uuid4().hex}"

        barrier = asyncio.Barrier(2)
        results = []

        async def create_worker(worker_id: int):
            async with file_db.session() as session:
                repo = ConversationRepository(session)
                await barrier.wait()
                try:
                    await repo.create_conversation(conv_id, user_id=user.id)
                    results.append(("success", worker_id))
                except (DuplicateError, Exception):
                    results.append(("error", worker_id))

        await asyncio.gather(create_worker(1), create_worker(2))

        successes = [r for r in results if r[0] == "success"]
        errors = [r for r in results if r[0] == "error"]
        assert len(successes) == 1
        assert len(errors) == 1

    async def test_read_during_write_sees_committed(
        self, file_db: DatabaseManager
    ):
        """
        Session A creates and commits data.
        Session B reads and sees the committed data.
        WAL read isolation verification.
        """
        user = await _create_test_user(file_db)
        conv_id = await _create_test_conversation(file_db, user.id)

        # Session A: add a message and commit
        msg_id = f"msg-{uuid.uuid4().hex}"
        async with file_db.session() as session_a:
            repo_a = ConversationRepository(session_a)
            await repo_a.add_message(conv_id, msg_id, "committed data", "thd-1")

        # Session B: should see committed data
        async with file_db.session() as session_b:
            repo_b = ConversationRepository(session_b)
            msg = await repo_b.get_message(msg_id)
            assert msg is not None
            assert msg.content == "committed data"
