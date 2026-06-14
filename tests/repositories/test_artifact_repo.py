"""
ArtifactRepository contract tests.

Covers session management, artifact CRUD, content updates,
version history, and batch operations.
"""

import uuid

import pytest

from db.models import (
    User,
    Conversation,
    Artifact,
    ArtifactVersion,
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
        assert md_only[0].content_type == "text/markdown"

    async def test_list_artifacts_returns_orm_objects(
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
        assert arts[0].content == long_content


# ============================================================
# Content updates (upsert_artifact_content)
# ============================================================


class TestContentUpdates:

    async def test_upsert_increments_version(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, artifact = sample_artifact
        assert artifact.current_version == 1

        updated = await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "v2 content", "update"
        )
        assert updated.current_version == 2

    async def test_upsert_creates_version_record(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "v2 content", "update"
        )

        ver = await artifact_repo.get_version(session_id, artifact_id, 2)
        assert ver is not None
        assert ver.content == "v2 content"
        assert ver.update_type == "update"

    async def test_upsert_nonexistent_raises(
        self, artifact_repo: ArtifactRepository, artifact_session: str
    ):
        with pytest.raises(NotFoundError):
            await artifact_repo.upsert_artifact_content(
                artifact_session, "nonexistent", "content", "update"
            )

    async def test_upsert_rewrite(
        self, artifact_repo: ArtifactRepository, sample_artifact
    ):
        session_id, artifact_id, _ = sample_artifact
        result = await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "completely new", "rewrite"
        )
        assert result.content == "completely new"
        assert result.current_version == 2

        ver = await artifact_repo.get_version(session_id, artifact_id, 2)
        assert ver.update_type == "rewrite"


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
        await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "v2", "update"
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
        await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "v2", "update"
        )
        await artifact_repo.upsert_artifact_content(
            session_id, artifact_id, "v3", "update"
        )

        versions = await artifact_repo.list_versions(session_id, artifact_id)
        assert len(versions) == 3
        assert [v.version for v in versions] == [1, 2, 3]
        assert versions[0].update_type == "create"
        assert versions[1].update_type == "update"


# ============================================================
# Storage quota aggregation (blob bytes)
# ============================================================


class TestBlobByteAggregation:
    """get_user_blob_bytes / get_blob_bytes_by_sessions — the quota + display source."""

    async def _conv_with_blob(
        self,
        conversation_repo: ConversationRepository,
        artifact_repo: ArtifactRepository,
        user_id: str,
        blob_size: int,
    ) -> str:
        """Create a conversation owning one blob-backed artifact of `blob_size` bytes."""
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(conversation_id=conv_id, user_id=user_id)
        await artifact_repo.create_artifact(
            session_id=conv_id,
            artifact_id=f"art-{uuid.uuid4().hex}",
            content_type="image/png",
            title="blob",
            content="",
            blob=b"x" * blob_size,
        )
        return conv_id

    async def test_user_bytes_sums_across_conversations(
        self,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
        test_user: User,
    ):
        await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 100)
        await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 250)

        total = await artifact_repo.get_user_blob_bytes(test_user.id)
        assert total == 350

    async def test_user_bytes_isolated_per_user(
        self,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
        test_user: User,
        test_admin: User,
    ):
        await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 100)
        await self._conv_with_blob(conversation_repo, artifact_repo, test_admin.id, 999)

        # Each user only sees their own blobs (no cross-user leak in the join).
        assert await artifact_repo.get_user_blob_bytes(test_user.id) == 100
        assert await artifact_repo.get_user_blob_bytes(test_admin.id) == 999

    async def test_user_bytes_zero_when_no_blobs(
        self,
        artifact_repo: ArtifactRepository,
        artifact_session: str,
        test_user: User,
    ):
        # artifact_session has a conversation but no blob-backed artifact.
        assert await artifact_repo.get_user_blob_bytes(test_user.id) == 0

    async def test_text_artifact_not_counted(
        self,
        artifact_repo: ArtifactRepository,
        sample_artifact,
        test_user: User,
    ):
        # sample_artifact is a pure-text artifact (no blob) → contributes 0.
        assert await artifact_repo.get_user_blob_bytes(test_user.id) == 0

    async def test_by_sessions_groups_and_defaults(
        self,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
        test_user: User,
    ):
        c1 = await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 100)
        c2 = await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 250)

        sizes = await artifact_repo.get_blob_bytes_by_sessions([c1, c2, "conv-absent"])
        assert sizes[c1] == 100
        assert sizes[c2] == 250
        # Sessions with no blob are absent (caller defaults to 0 via .get).
        assert "conv-absent" not in sizes

    async def test_by_sessions_empty_input_short_circuits(
        self, artifact_repo: ArtifactRepository
    ):
        assert await artifact_repo.get_blob_bytes_by_sessions([]) == {}

    async def test_for_session_sums_owner_across_all_sessions(
        self,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
        test_user: User,
    ):
        # The single-query chokepoint helper: given ANY one of the owner's
        # sessions, it returns the owner's FULL total across all their sessions.
        c1 = await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 100)
        await self._conv_with_blob(conversation_repo, artifact_repo, test_user.id, 250)
        assert await artifact_repo.get_user_blob_bytes_for_session(c1) == 350

    async def test_for_session_ownerless_returns_zero(
        self,
        artifact_repo: ArtifactRepository,
        conversation_repo: ConversationRepository,
    ):
        # Ownerless conversation → owner subquery is NULL → no rows match → 0.
        # (The chokepoint's numeric check still bounds a single oversized blob.)
        conv_id = f"conv-{uuid.uuid4().hex}"
        await conversation_repo.create_conversation(conversation_id=conv_id, user_id=None)
        await artifact_repo.create_artifact(
            session_id=conv_id, artifact_id="a.bin",
            content_type="application/octet-stream", title="b", content="",
            blob=b"x" * 500,
        )
        assert await artifact_repo.get_user_blob_bytes_for_session(conv_id) == 0


# ============================================================
# Batch operations
# ============================================================
