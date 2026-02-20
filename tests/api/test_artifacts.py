"""
Artifacts API integration tests.

Data seeded directly via repository methods (not through POST /chat).
"""

import uuid
from typing import Tuple

import pytest
from httpx import AsyncClient

from db.models import User
from db.database import DatabaseManager
from repositories.conversation_repo import ConversationRepository
from repositories.artifact_repo import ArtifactRepository


# ============================================================
# Local fixtures
# ============================================================


@pytest.fixture
async def seed_artifacts(
    db_manager: DatabaseManager, test_user: User
) -> Tuple[str, str]:
    """
    Seed a conversation + ArtifactSession + artifact (2 versions).

    Returns (session_id, artifact_id).
    """
    async with db_manager.session() as session:
        conv_repo = ConversationRepository(session)
        art_repo = ArtifactRepository(session)

        conv_id = f"conv-{uuid.uuid4().hex}"
        await conv_repo.create_conversation(
            conversation_id=conv_id,
            title="Artifact Test",
            user_id=test_user.id,
        )

        artifact_id = f"art-{uuid.uuid4().hex}"
        await art_repo.create_artifact(
            session_id=conv_id,
            artifact_id=artifact_id,
            content_type="markdown",
            title="Test Artifact",
            content="# Version 1",
        )

        # Create v2
        await art_repo.update_artifact_content(
            session_id=conv_id,
            artifact_id=artifact_id,
            new_content="# Version 2",
            update_type="update",
            expected_lock_version=1,
        )

    return conv_id, artifact_id


# ============================================================
# List artifacts
# ============================================================


class TestListArtifacts:

    async def test_list_success(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, _ = seed_artifacts
        resp = await client.get(f"/api/v1/artifacts/{session_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == session_id
        assert len(body["artifacts"]) == 1

    async def test_list_cross_user(
        self, admin_client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, _ = seed_artifacts
        resp = await admin_client.get(f"/api/v1/artifacts/{session_id}")
        assert resp.status_code == 404

    async def test_list_nonexistent_session(self, client: AsyncClient):
        resp = await client.get("/api/v1/artifacts/nonexistent-session")
        assert resp.status_code == 404

    async def test_list_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/artifacts/some-session")
        assert resp.status_code == 401


# ============================================================
# Artifact detail
# ============================================================


class TestArtifactDetail:

    async def test_get_detail(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await client.get(f"/api/v1/artifacts/{session_id}/{artifact_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == artifact_id
        assert body["content"] == "# Version 2"
        assert body["current_version"] == 2

    async def test_get_not_found(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, _ = seed_artifacts
        resp = await client.get(f"/api/v1/artifacts/{session_id}/nonexistent-art")
        assert resp.status_code == 404

    async def test_get_cross_user(
        self, admin_client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await admin_client.get(f"/api/v1/artifacts/{session_id}/{artifact_id}")
        assert resp.status_code == 404


# ============================================================
# Version operations
# ============================================================


class TestVersions:

    async def test_list_versions(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}/versions"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["versions"]) == 2
        assert body["versions"][0]["version"] == 1
        assert body["versions"][1]["version"] == 2

    async def test_list_versions_artifact_not_found(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, _ = seed_artifacts
        resp = await client.get(
            f"/api/v1/artifacts/{session_id}/nonexistent-art/versions"
        )
        assert resp.status_code == 404

    async def test_get_version_detail(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}/versions/1"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 1
        assert body["content"] == "# Version 1"
        assert body["update_type"] == "create"

    async def test_get_version_not_found(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}/versions/999"
        )
        assert resp.status_code == 404

    async def test_versions_cross_user(
        self, admin_client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await admin_client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}/versions"
        )
        assert resp.status_code == 404
