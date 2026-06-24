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
            content_type="text/markdown",
            title="Test Artifact",
            content="# Version 1",
        )

        # Create v2
        await art_repo.upsert_artifact_content(
            session_id=conv_id,
            artifact_id=artifact_id,
            new_content="# Version 2",
            update_type="update",
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

    async def test_detail_includes_versions(
        self, client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        """getArtifact response includes versions list and current content."""
        session_id, artifact_id = seed_artifacts
        resp = await client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["versions"]) == 2
        assert body["versions"][0]["version"] == 1
        assert body["versions"][1]["version"] == 2
        # current_version + content together replace the removed latest_version field
        assert body["current_version"] == 2
        assert body["content"] == "# Version 2"

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

    async def test_version_detail_cross_user(
        self, admin_client: AsyncClient, seed_artifacts: Tuple[str, str]
    ):
        session_id, artifact_id = seed_artifacts
        resp = await admin_client.get(
            f"/api/v1/artifacts/{session_id}/{artifact_id}/versions/1"
        )
        assert resp.status_code == 404


class TestUploadSizeGuard:
    """convert_and_create_artifact must reject an oversize part BEFORE reading
    it into RAM — otherwise a 1GB upload spikes memory just to be 422'd, even
    though multipart parsing kept it spooled on disk."""

    class _FakeUpload:
        """Minimal UploadFile stand-in: tracks whether read() was awaited."""

        def __init__(self, size, read_bytes=b"", filename="big.bin"):
            self.size = size
            self.filename = filename
            self._read_bytes = read_bytes
            self.read_called = False

        async def read(self):
            self.read_called = True
            return self._read_bytes

    async def test_oversize_rejected_before_read(self, monkeypatch):
        from fastapi import HTTPException
        from config import config as cfg
        from api.routers import artifacts as art

        monkeypatch.setattr(cfg, "MAX_UPLOAD_SIZE", 10)
        f = self._FakeUpload(size=11)  # parser-reported part length over the cap
        with pytest.raises(HTTPException) as ei:
            # The size guard lives in convert_uploaded_file (phase 1, pure
            # transform). The old convert_and_create_artifact wrapper was removed
            # when uploads moved to in-engine staging (no immediate commit).
            await art.convert_uploaded_file(f)
        assert ei.value.status_code == 422
        assert "too large" in ei.value.detail.lower()
        # The whole point: the body was never materialized.
        assert f.read_called is False

    async def test_fallback_len_check_when_size_unset(self, monkeypatch):
        from fastapi import HTTPException
        from config import config as cfg
        from api.routers import artifacts as art

        monkeypatch.setattr(cfg, "MAX_UPLOAD_SIZE", 10)
        # .size is None → pre-check skipped; the post-read len() guard catches it.
        f = self._FakeUpload(size=None, read_bytes=b"a" * 11)
        with pytest.raises(HTTPException) as ei:
            # The size guard lives in convert_uploaded_file (phase 1, pure
            # transform). The old convert_and_create_artifact wrapper was removed
            # when uploads moved to in-engine staging (no immediate commit).
            await art.convert_uploaded_file(f)
        assert ei.value.status_code == 422
        assert f.read_called is True


# ============================================================
# has_blob:二进制 artifact 在用户路由与 admin 路由都要标对(C-0)
# ============================================================

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
async def seed_blob_artifact(
    db_manager: DatabaseManager, test_user: User
) -> Tuple[str, str]:
    """Seed a conversation + blob-only docx artifact. Returns (session_id, artifact_id)."""
    async with db_manager.session() as session:
        conv_repo = ConversationRepository(session)
        art_repo = ArtifactRepository(session)

        conv_id = f"conv-{uuid.uuid4().hex}"
        await conv_repo.create_conversation(
            conversation_id=conv_id,
            title="Blob Artifact Test",
            user_id=test_user.id,
        )

        artifact_id = f"doc-{uuid.uuid4().hex}"
        await art_repo.create_artifact(
            session_id=conv_id,
            artifact_id=artifact_id,
            content_type=_DOCX_MIME,
            title="spec",
            content="",
            metadata={"original_filename": "spec.docx"},
            source="user_upload",
            blob=b"PK\x03\x04" + b"\x00" * 16,
        )

    return conv_id, artifact_id


class TestHasBlobField:
    """has_blob 取自 Artifact.has_blob 列(repo 建行时按 blob 在场写死);admin 路由
    复用同一 schema,填充点独立 —— 两条路由都锁住。"""

    async def test_user_routes_mark_blob(
        self, client: AsyncClient, seed_blob_artifact: Tuple[str, str],
        seed_artifacts: Tuple[str, str],
    ):
        session_id, artifact_id = seed_blob_artifact
        resp = await client.get(f"/api/v1/artifacts/{session_id}")
        assert resp.status_code == 200
        (item,) = resp.json()["artifacts"]
        assert item["has_blob"] is True

        resp = await client.get(f"/api/v1/artifacts/{session_id}/{artifact_id}")
        assert resp.status_code == 200
        assert resp.json()["has_blob"] is True

        # 纯文本 artifact 对照:has_blob=False
        text_session, text_artifact = seed_artifacts
        resp = await client.get(f"/api/v1/artifacts/{text_session}/{text_artifact}")
        assert resp.json()["has_blob"] is False

    async def test_admin_routes_mark_blob(
        self, admin_client: AsyncClient, seed_blob_artifact: Tuple[str, str]
    ):
        session_id, artifact_id = seed_blob_artifact
        resp = await admin_client.get(
            f"/api/v1/admin/conversations/{session_id}/artifacts"
        )
        assert resp.status_code == 200
        (item,) = resp.json()["artifacts"]
        assert item["has_blob"] is True

        resp = await admin_client.get(
            f"/api/v1/admin/conversations/{session_id}/artifacts/{artifact_id}"
        )
        assert resp.status_code == 200
        assert resp.json()["has_blob"] is True
