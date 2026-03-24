"""
ArtifactManager write-back contract tests.

Covers the design invariant: execution-time edits are memory-only,
flush_all persists a single final snapshot per artifact, and version
numbers may be sparse (intermediate in-memory versions are not recorded).
"""

import uuid

import pytest

from db.models import User
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from tools.builtin.artifact_ops import ArtifactManager


@pytest.fixture
async def session_id(conversation_repo: ConversationRepository, test_user: User) -> str:
    """Create a conversation (auto-creates ArtifactSession), return session_id."""
    conv_id = f"conv-{uuid.uuid4().hex}"
    await conversation_repo.create_conversation(
        conversation_id=conv_id, user_id=test_user.id
    )
    return conv_id


@pytest.fixture
def artifact_manager(artifact_repo: ArtifactRepository) -> ArtifactManager:
    return ArtifactManager(artifact_repo)


class TestWriteBackFlush:
    """Verify that flush_all collapses in-memory edits into a single DB version."""

    async def test_create_then_updates_produce_single_version(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """create -> update -> update -> flush produces one version record at v3."""
        artifact_manager.set_session(session_id)

        # In-memory create (v1)
        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            content_type="text/markdown",
            title="Plan",
            content="# Step 1",
        )
        assert ok

        # In-memory update (v2)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1",
            new_str="# Step 1\n# Step 2",
        )
        assert ok

        # In-memory update (v3)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="task_plan",
            old_str="# Step 1\n# Step 2",
            new_str="# Step 1\n# Step 2\n# Step 3",
        )
        assert ok

        # Verify memory state
        memory = await artifact_manager.get_artifact(session_id, "task_plan")
        assert memory is not None
        assert memory.current_version == 3

        # DB should have nothing yet
        db_art = await artifact_repo.get_artifact(session_id, "task_plan")
        assert db_art is None

        # Flush
        await artifact_manager.flush_all(session_id)

        # DB should now have the artifact at v3
        db_art = await artifact_repo.get_artifact(session_id, "task_plan")
        assert db_art is not None
        assert db_art.current_version == 3
        assert db_art.content == "# Step 1\n# Step 2\n# Step 3"

        # Only one version record should exist (the final snapshot)
        versions = await artifact_repo.list_versions(session_id, "task_plan")
        assert len(versions) == 1
        assert versions[0].version == 3
        assert versions[0].update_type == "create"
        assert versions[0].content == "# Step 1\n# Step 2\n# Step 3"

    async def test_existing_artifact_update_flush(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """Pre-existing artifact updated twice in-memory flushes as one new version."""
        # Pre-create in DB (v1)
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="report",
            content_type="text/markdown",
            title="Report",
            content="initial",
        )

        artifact_manager.set_session(session_id)

        # Two in-memory updates (v2, v3)
        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="initial",
            new_str="updated once",
        )
        assert ok

        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="updated once",
            new_str="updated twice",
        )
        assert ok

        await artifact_manager.flush_all(session_id)

        db_art = await artifact_repo.get_artifact(session_id, "report")
        assert db_art.current_version == 3
        assert db_art.content == "updated twice"

        # Two version records: v1 (original create) + v3 (flushed update)
        # v2 is skipped — sparse version numbers are by design
        versions = await artifact_repo.list_versions(session_id, "report")
        assert len(versions) == 2
        assert [v.version for v in versions] == [1, 3]

    async def test_flush_is_idempotent(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """Calling flush_all twice does not create duplicate records."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="doc",
            content_type="text/markdown",
            title="Doc",
            content="hello",
        )
        assert ok

        await artifact_manager.flush_all(session_id)
        await artifact_manager.flush_all(session_id)  # no-op

        versions = await artifact_repo.list_versions(session_id, "doc")
        assert len(versions) == 1


class TestWriteBackInventory:
    """Verify that list_artifacts merges in-memory state during execution."""

    async def test_list_includes_unflushed_new_artifact(
        self, artifact_manager: ArtifactManager, session_id: str
    ):
        """New in-memory artifact appears in list_artifacts before flush."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="plan",
            content_type="text/markdown",
            title="Plan",
            content="# Plan",
        )
        assert ok

        artifacts = await artifact_manager.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["id"] == "plan"
        assert artifacts[0]["content"] == "# Plan"

    async def test_list_shows_dirty_content_over_db(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """In-memory edits override DB content in list_artifacts."""
        # Pre-create in DB
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="report",
            content_type="text/markdown",
            title="Report",
            content="old content",
        )

        artifact_manager.set_session(session_id)

        ok, _, _ = await artifact_manager.update_artifact(
            session_id=session_id,
            artifact_id="report",
            old_str="old content",
            new_str="new content",
        )
        assert ok

        artifacts = await artifact_manager.list_artifacts(session_id)
        assert len(artifacts) == 1
        assert artifacts[0]["content"] == "new content"
        assert artifacts[0]["version"] == 2


class TestWriteBackFlushFailure:
    """Verify that failed flushes retain dirty state."""

    async def test_failed_flush_keeps_dirty(
        self, artifact_manager: ArtifactManager, artifact_repo: ArtifactRepository, session_id: str
    ):
        """If flush fails for one artifact, it stays in dirty set."""
        artifact_manager.set_session(session_id)

        ok, _ = await artifact_manager.create_artifact(
            session_id=session_id,
            artifact_id="will_fail",
            content_type="text/markdown",
            title="Fail",
            content="content",
        )
        assert ok

        # Sabotage: pre-create the same artifact in DB so flush hits DuplicateError
        await artifact_repo.create_artifact(
            session_id=session_id,
            artifact_id="will_fail",
            content_type="text/markdown",
            title="Existing",
            content="existing",
        )

        with pytest.raises(RuntimeError, match="Failed to flush"):
            await artifact_manager.flush_all(session_id)

        # Dirty entry should still be present
        assert (session_id, "will_fail") in artifact_manager._dirty
