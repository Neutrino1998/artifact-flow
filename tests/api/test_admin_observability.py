"""Admin conversation observability: active execution discovery + live SSE."""

import uuid

import pytest
from httpx import AsyncClient

from api.dependencies import get_runtime_store, get_stream_transport
from config import config
from core.management.conversation_manager import ConversationManager
from db.database import DatabaseManager
from db.models import User
from repositories.conversation_repo import ConversationRepository


@pytest.fixture
async def observed_conversation(
    db_manager: DatabaseManager,
    test_user: User,
) -> tuple[str, str]:
    conv_id = f"conv-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        await repo.create_conversation(
            conversation_id=conv_id,
            title="Observed conversation",
            user_id=test_user.id,
        )
        await repo.add_message(
            conversation_id=conv_id,
            message_id=message_id,
            user_input="watch me",
            metadata={
                "uploaded_files": [
                    {"id": "brief.docx", "filename": "Brief.docx"},
                ],
            },
        )
    return conv_id, message_id


async def _make_active(app, conv_id: str, message_id: str, owner_id: str):
    store = app.dependency_overrides[get_runtime_store]()
    transport = app.dependency_overrides[get_stream_transport]()
    assert await store.try_acquire_lease(conv_id, message_id) is None
    await transport.create_stream(message_id, owner_user_id=owner_id)
    return store, transport


class TestAdminConversationActivity:
    async def test_privacy_mode_redacts_owner_and_upload_references(
        self,
        monkeypatch,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
        conv_id, _ = observed_conversation

        listing = await admin_client.get("/api/v1/admin/conversations")
        assert listing.status_code == 200
        summary = next(
            item for item in listing.json()["conversations"] if item["id"] == conv_id
        )
        assert summary["user_id"] is None
        assert summary["user_display_name"] == "匿名用户"

        detail = await admin_client.get(f"/api/v1/admin/conversations/{conv_id}/events")
        assert detail.status_code == 200
        body = detail.json()
        assert body["user_id"] is None
        assert body["user_display_name"] == "匿名用户"
        assert body["messages"][0]["uploaded_files"] == [{
            "id": None,
            "filename": "上传文件 1",
            "content_accessible": False,
        }]
        assert "Brief.docx" not in detail.text
        assert test_user.id not in detail.text

    async def test_privacy_mode_disables_owner_filter(
        self,
        monkeypatch,
        admin_client: AsyncClient,
        test_user: User,
    ):
        monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
        response = await admin_client.get(
            "/api/v1/admin/conversations",
            params={"user_id": test_user.id},
        )
        assert response.status_code == 400

    async def test_privacy_mode_keeps_prompt_reconstruction(
        self,
        monkeypatch,
        admin_client: AsyncClient,
        observed_conversation: tuple[str, str],
    ):
        monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
        conv_id, message_id = observed_conversation

        async def reconstruct_prompt(
            _self,
            requested_conv_id,
            requested_message_id,
            event_id,
        ):
            return {
                "conversation_id": requested_conv_id,
                "message_id": requested_message_id,
                "agent_start_event_id": event_id,
                "agent_name": "lead_agent",
                "model": "test-model",
                "exposed_tool_names": ["read_artifact"],
                "has_reminder": True,
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "diagnostic prompt snapshot"},
                ],
            }

        monkeypatch.setattr(
            ConversationManager,
            "reconstruct_prompt",
            reconstruct_prompt,
        )

        response = await admin_client.get(
            f"/api/v1/admin/conversations/{conv_id}/messages/{message_id}/reconstruct",
            params={"agent_start_event_id": "evt-anchor"},
        )

        assert response.status_code == 200
        assert response.json()["messages"][-1]["content"] == "diagnostic prompt snapshot"

    async def test_list_and_detail_expose_active_message_id(
        self,
        app,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        conv_id, message_id = observed_conversation
        store, transport = await _make_active(app, conv_id, message_id, test_user.id)
        try:
            listing = await admin_client.get("/api/v1/admin/conversations")
            assert listing.status_code == 200
            summary = next(
                item
                for item in listing.json()["conversations"]
                if item["id"] == conv_id
            )
            assert summary["is_active"] is True
            assert summary["active_message_id"] == message_id

            detail = await admin_client.get(f"/api/v1/admin/conversations/{conv_id}/events")
            assert detail.status_code == 200
            assert detail.json()["is_active"] is True
            assert detail.json()["active_message_id"] == message_id
            assert detail.json()["messages"][0]["uploaded_files"] == [
                {
                    "id": "brief.docx",
                    "filename": "Brief.docx",
                    "content_accessible": True,
                },
            ]
        finally:
            await transport.close_stream(message_id)
            await store.release_lease(conv_id, message_id)

    async def test_admin_can_subscribe_to_owner_stream(
        self,
        app,
        client: AsyncClient,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        conv_id, message_id = observed_conversation
        store, transport = await _make_active(app, conv_id, message_id, test_user.id)
        await transport.push_event(message_id, {
            "type": "agent_start",
            "timestamp": "2026-07-13T00:00:00",
            "agent": "lead_agent",
            "data": {
                "agent": "lead_agent",
                "system_prompt": "admin-only system prompt",
                "reminder": "admin-only dynamic reminder",
                "future_internal_field": "admin-only future context",
            },
        })
        await transport.push_event(message_id, {
            "type": "tool_start",
            "timestamp": "2026-07-13T00:00:01",
            "agent": "lead_agent",
            "data": {"tool": "fetch"},
        })
        await transport.push_event(message_id, {
            "type": "complete",
            "timestamp": "2026-07-13T00:00:02",
            "data": {"success": True, "message_id": message_id},
        })
        try:
            user_response = await client.get(f"/api/v1/stream/{message_id}")
            assert user_response.status_code == 200
            assert "event: agent_start" in user_response.text
            assert "admin-only system prompt" not in user_response.text
            assert "admin-only dynamic reminder" not in user_response.text
            assert "admin-only future context" not in user_response.text

            response = await admin_client.get(
                f"/api/v1/admin/conversations/{conv_id}/stream"
            )
            assert response.status_code == 200
            assert "event: agent_start" in response.text
            assert "admin-only system prompt" in response.text
            assert "admin-only dynamic reminder" in response.text
            assert "admin-only future context" in response.text
            assert "event: tool_start" in response.text
            assert "event: complete" in response.text
        finally:
            await transport.close_stream(message_id)
            await store.release_lease(conv_id, message_id)

    async def test_privacy_mode_suppresses_artifact_events_in_admin_stream(
        self,
        monkeypatch,
        app,
        admin_client: AsyncClient,
        test_user: User,
        observed_conversation: tuple[str, str],
    ):
        monkeypatch.setattr(config, "ADMIN_PRIVACY_MODE", True)
        conv_id, message_id = observed_conversation
        store, transport = await _make_active(app, conv_id, message_id, test_user.id)
        await transport.push_event(message_id, {
            "type": "metadata",
            "timestamp": "2026-07-13T00:00:00",
            "data": {
                "conversation_id": conv_id,
                "message_id": message_id,
                "uploaded_files": [{"filename": "Payroll-Alice.xlsx"}],
            },
        })
        await transport.push_event(message_id, {
            "type": "artifact_created",
            "timestamp": "2026-07-13T00:00:01",
            "data": {
                "id": "payroll-alice",
                "title": "Payroll-Alice.xlsx",
                "source": "user_upload",
                "original_filename": "Payroll-Alice.xlsx",
                "content": "private-created-content",
            },
        })
        await transport.push_event(message_id, {
            "type": "artifact_updated",
            "timestamp": "2026-07-13T00:00:02",
            "data": {
                "id": "payroll-alice",
                "content": "private-updated-content",
                "delta": {
                    "offset": 0,
                    "deleted_len": 0,
                    "inserted_text": "private-delta-content",
                },
            },
        })
        await transport.push_event(message_id, {
            "type": "tool_complete",
            "timestamp": "2026-07-13T00:00:03",
            "data": {
                "tool": "read_artifact",
                "success": True,
                "result_data": "semantic-diagnostic-content",
            },
        })
        await transport.push_event(message_id, {
            "type": "complete",
            "timestamp": "2026-07-13T00:00:04",
            "data": {"success": True, "message_id": message_id},
        })
        try:
            response = await admin_client.get(
                f"/api/v1/admin/conversations/{conv_id}/stream"
            )
            assert response.status_code == 200
            assert "Payroll-Alice.xlsx" not in response.text
            assert "payroll-alice" not in response.text
            assert "private-created-content" not in response.text
            assert "private-updated-content" not in response.text
            assert "private-delta-content" not in response.text
            assert "上传文件 1" in response.text
            assert "event: artifact_created" not in response.text
            assert "event: artifact_updated" not in response.text
            assert "event: tool_complete" in response.text
            assert "semantic-diagnostic-content" in response.text
        finally:
            await transport.close_stream(message_id)
            await store.release_lease(conv_id, message_id)

    async def test_regular_user_cannot_open_admin_stream(
        self,
        client: AsyncClient,
        observed_conversation: tuple[str, str],
    ):
        conv_id, _ = observed_conversation
        response = await client.get(f"/api/v1/admin/conversations/{conv_id}/stream")
        assert response.status_code == 403

    async def test_inactive_conversation_has_no_admin_stream(
        self,
        admin_client: AsyncClient,
        observed_conversation: tuple[str, str],
    ):
        conv_id, _ = observed_conversation
        response = await admin_client.get(f"/api/v1/admin/conversations/{conv_id}/stream")
        assert response.status_code == 404
