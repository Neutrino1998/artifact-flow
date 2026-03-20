"""
ArtifactRepository contract tests.

Covers session management, artifact CRUD, optimistic locking,
version history, and batch operations.
"""

import uuid

import pytest

from db.models import (
    User,
    Conversation,
    Artifact,
    ArtifactVersion,
    VersionConflictError,
)
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from repositories.base import NotFoundError, DuplicateError


# ============================================================
# Local fixtures
# ============================================================


@pytest.fixture
async def artifact_session(
    conversation_repo: ConversationRepository, test_user: User
) -> str:
    """Create a conversation (auto-creates ArtifactSession), return session_id."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
async def sample_artifact(artifact_session: str, artifact_repo: ArtifactRepository):
    """Create an artifact in the session, return (session_id, artifact_id, artifact)."""
    artifact_id = f"art-{uuid.uuid4().hex}"
    artifact = await artifact_repo.create_artifact(
        session_id=artifact_session,
        artifact_id=artifact_id,
        content_type="text/markdown",
        title="Sample Artifact",
        content="# Hello World",
    )
    return artifact_session, artifact_id, artifact


# ============================================================
# Session operations
# ============================================================


class TestSession:

    async def test_get_session_exists(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        session = await artifact_repo.get_session(artifact_session)
        assert session is not None
        assert session.id == artifact_session

    async def test_get_session_nonexistent(self, artifact_repo: ArtifactRepository):
        session = await artifact_repo.get_session("nonexistent")
        assert session is None

    async def test_get_session_or_raise(self, artifact_repo: ArtifactRepository):
        with pytest.raises(NotFoundError):
            await artifact_repo.get_session_or_raise("nonexistent")

    async def test_ensure_session_exists_idempotent(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        # Session already exists via conversation creation
        session = await artifact_repo.ensure_session_exists(artifact_session)
        assert session.id == artifact_session

        # Call again — should be idempotent
        session2 = await artifact_repo.ensure_session_exists(artifact_session)
        assert session2.id == artifact_session


# ============================================================
# Artifact CRUD
# ============================================================


class TestArtifactCRUD:

    async def test_create_artifact_with_initial_version(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        artifact_id = f"art-{uuid.uuid4().hex}"
        artifact = await artifact_repo.create_artifact(
            session_id=artifact_session,
            artifact_id=artifact_id,
            content_type="text/x-python",
            title="My Script",
            content="print('hello')",
        )

        assert artifact.id == artifact_id
        assert artifact.current_version == 1
        assert artifact.lock_version == 1
        assert artifact.content == "print('hello')"

        # v1 version record should exist
        ver = await artifact_repo.get_version(artifact_session, artifact_id, 1)
        assert ver is not None
        assert ver.content == "print('hello')"
        assert ver.update_type == "create"

    async def test_create_artifact_duplicate_raises(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        with pytest.raises(DuplicateError):
            await artifact_repo.create_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                content_type="text/markdown",
                title="Dup",
                content="dup",
            )

    async def test_create_artifact_nonexistent_session(
        self, artifact_repo: ArtifactRepository
    ):
        with pytest.raises(NotFoundError):
            await artifact_repo.create_artifact(
                session_id="nonexistent",
                artifact_id="art-x",
                content_type="text/markdown",
                title="X",
                content="x",
            )

    async def test_get_artifact_not_found(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        result = await artifact_repo.get_artifact(artifact_session, "nonexistent")
        assert result is None

    async def test_get_artifact_or_raise(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        with pytest.raises(NotFoundError):
            await artifact_repo.get_artifact_or_raise(artifact_session, "nonexistent")

    async def test_list_artifacts_content_type_filter(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        await artifact_repo.create_artifact(
            artifact_session, f"art-md-{uuid.uuid4().hex[:8]}", "text/markdown", "MD", "# md"
        )
        await artifact_repo.create_artifact(
            artifact_session, f"art-py-{uuid.uuid4().hex[:8]}", "text/x-python", "PY", "x=1"
        )

        all_arts = await artifact_repo.list_artifacts(artifact_session)
        assert len(all_arts) == 2

        md_only = await artifact_repo.list_artifacts(
            artifact_session, content_type="text/markdown"
        )
        assert len(md_only) == 1
        assert md_only[0]["content_type"] == "text/markdown"

    async def test_list_artifacts_returns_full_content(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        long_content = "x" * 300
        await artifact_repo.create_artifact(
            artifact_session,
            f"art-{uuid.uuid4().hex[:8]}",
            "text/markdown",
            "Long",
            long_content,
        )

        arts = await artifact_repo.list_artifacts(artifact_session)
        assert len(arts) == 1
        assert arts[0]["content"] == long_content

    async def test_delete_artifact_cascades_versions(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact

        # Update to create v2
        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "updated", "update", expected_lock_version=1
        )

        result = await artifact_repo.delete_artifact(session_id, artifact_id)
        assert result is True

        # Artifact and versions should be gone
        assert await artifact_repo.get_artifact(session_id, artifact_id) is None
        assert await artifact_repo.get_version(session_id, artifact_id, 1) is None
        assert await artifact_repo.get_version(session_id, artifact_id, 2) is None

    async def test_delete_artifact_nonexistent(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        result = await artifact_repo.delete_artifact(artifact_session, "nonexistent")
        assert result is False


# ============================================================
# Optimistic locking (PG migration critical)
# ============================================================


class TestOptimisticLocking:

    async def test_update_content_increments_versions(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, artifact = sample_artifact
        assert artifact.current_version == 1
        assert artifact.lock_version == 1

        updated = await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v2 content", "update", expected_lock_version=1
        )
        assert updated.current_version == 2
        assert updated.lock_version == 2

    async def test_update_content_creates_version_record(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v2 content", "update", expected_lock_version=1
        )

        ver = await artifact_repo.get_version(session_id, artifact_id, 2)
        assert ver is not None
        assert ver.content == "v2 content"
        assert ver.update_type == "update"

    async def test_update_content_wrong_lock_raises(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact

        with pytest.raises(VersionConflictError) as exc_info:
            await artifact_repo.update_artifact_content(
                session_id, artifact_id, "bad", "update", expected_lock_version=999
            )
        assert exc_info.value.artifact_id == artifact_id
        assert exc_info.value.expected_version == 999

    async def test_rewrite_artifact(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        result = await artifact_repo.rewrite_artifact(
            session_id, artifact_id, "completely new", expected_lock_version=1
        )
        assert result.content == "completely new"
        assert result.current_version == 2

        ver = await artifact_repo.get_version(session_id, artifact_id, 2)
        assert ver.update_type == "rewrite"

    async def test_update_title_no_version_change(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, artifact = sample_artifact
        old_version = artifact.current_version
        old_lock = artifact.lock_version

        updated = await artifact_repo.update_artifact_title(
            session_id, artifact_id, "New Title"
        )
        assert updated.title == "New Title"
        assert updated.current_version == old_version
        assert updated.lock_version == old_lock


# ============================================================
# Version history
# ============================================================


class TestVersionHistory:

    async def test_get_version_specific(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        ver = await artifact_repo.get_version(session_id, artifact_id, 1)
        assert ver is not None
        assert ver.version == 1
        assert ver.content == "# Hello World"

    async def test_get_version_content_current(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        content = await artifact_repo.get_version_content(session_id, artifact_id)
        assert content == "# Hello World"

    async def test_get_version_content_historical(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact

        # Create v2
        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v2", "update", expected_lock_version=1
        )

        # Get v1 content
        content = await artifact_repo.get_version_content(
            session_id, artifact_id, version=1
        )
        assert content == "# Hello World"

        # Get current (v2)
        current = await artifact_repo.get_version_content(session_id, artifact_id)
        assert current == "v2"

    async def test_list_versions_ordered(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact

        # Create v2 and v3
        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v2", "update", expected_lock_version=1
        )
        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v3", "update", expected_lock_version=2
        )

        versions = await artifact_repo.list_versions(session_id, artifact_id)
        assert len(versions) == 3
        assert [v["version"] for v in versions] == [1, 2, 3]
        assert versions[0]["update_type"] == "create"
        assert versions[1]["update_type"] == "update"

    async def test_get_version_diff(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact

        await artifact_repo.update_artifact_content(
            session_id, artifact_id, "v2 content", "update", expected_lock_version=1
        )

        diff = await artifact_repo.get_version_diff(session_id, artifact_id, 1, 2)
        assert diff is not None
        assert diff["from_version"] == 1
        assert diff["to_version"] == 2
        assert diff["from_content"] == "# Hello World"
        assert diff["to_content"] == "v2 content"
        assert diff["to_update_type"] == "update"

    async def test_get_version_diff_nonexistent(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        diff = await artifact_repo.get_version_diff(session_id, artifact_id, 1, 999)
        assert diff is None


# ============================================================
# Batch operations
# ============================================================

