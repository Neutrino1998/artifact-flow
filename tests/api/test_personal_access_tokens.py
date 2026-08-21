"""PAT lifecycle, scope boundaries, and ordinary-user endpoint authorization."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from httpx import AsyncClient

from db.database import DatabaseManager
from db.models import PersonalAccessToken, User
from api.dependencies import get_runtime_store
from api.services.runtime_store import _InterruptState
from repositories.conversation_repo import ConversationRepository
from utils.time import utc_now


async def _create_pat(
    client: AsyncClient,
    scopes: list[str],
    *,
    name: str = "test automation",
) -> dict:
    response = await client.post(
        "/api/v1/auth/pats",
        json={"name": name, "scopes": scopes, "expires_in_days": 30},
    )
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _pat_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_pat_lifecycle_reveals_secret_once_and_revokes_immediately(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_user: User,
    db_manager: DatabaseManager,
):
    created = await _create_pat(
        client,
        ["conversations:read", "artifacts:read"],
    )
    assert created["token"].startswith("af_pat_")
    assert created["prefix"].startswith("af_pat_")
    assert created["revoked_at"] is None

    async with db_manager.session() as session:
        row = await session.get(PersonalAccessToken, created["id"])
        assert row is not None
        assert row.user_id == test_user.id
        assert row.secret_hash != created["token"]
        assert created["token"] not in row.secret_hash

    listed = await client.get("/api/v1/auth/pats")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    listed_item = listed.json()["tokens"][0]
    assert listed_item["id"] == created["id"]
    assert "token" not in listed_item

    me = await anon_client.get(
        "/api/v1/auth/me",
        headers=_pat_headers(created["token"]),
    )
    assert me.status_code == 200
    assert me.json()["id"] == test_user.id

    revoked = await client.delete(f"/api/v1/auth/pats/{created['id']}")
    assert revoked.status_code == 204
    assert (await client.delete(f"/api/v1/auth/pats/{created['id']}")).status_code == 204

    rejected = await anon_client.get(
        "/api/v1/auth/me",
        headers=_pat_headers(created["token"]),
    )
    assert rejected.status_code == 401


async def test_pat_scope_and_resource_ownership_are_both_required(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_user: User,
    test_admin: User,
    db_manager: DatabaseManager,
):
    async with db_manager.session() as session:
        own_conv = f"conv-{uuid.uuid4().hex}"
        other_conv = f"conv-{uuid.uuid4().hex}"
        repo = ConversationRepository(session)
        await repo.create_conversation(own_conv, "mine", user_id=test_user.id)
        await repo.create_conversation(other_conv, "other", user_id=test_admin.id)

    read_pat = await _create_pat(client, ["conversations:read"])
    headers = _pat_headers(read_pat["token"])

    listing = await anon_client.get("/api/v1/chat", headers=headers)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["conversations"]] == [own_conv]

    assert (
        await anon_client.get(f"/api/v1/chat/{other_conv}", headers=headers)
    ).status_code == 404
    assert (
        await anon_client.get(f"/api/v1/artifacts/{own_conv}", headers=headers)
    ).status_code == 403
    assert (
        await anon_client.post(
            "/api/v1/chat",
            headers=headers,
            data={"payload": json.dumps({"user_input": "hello"})},
        )
    ).status_code == 403


async def test_pat_write_scope_covers_chat_upload_but_not_reads(
    client: AsyncClient,
    anon_client: AsyncClient,
):
    write_pat = await _create_pat(client, ["conversations:write"])
    headers = _pat_headers(write_pat["token"])

    assert (await anon_client.get("/api/v1/chat", headers=headers)).status_code == 403

    # Attachment submission is the same conversation write operation: it passes
    # PAT scope admission and returns the normal asynchronous turn handle.
    response = await anon_client.post(
        "/api/v1/chat",
        headers=headers,
        data={"payload": json.dumps({"user_input": "inspect this"})},
        files={"files": ("bad.unsupported", b"payload", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["stream_url"].startswith("/api/v1/stream/")


async def test_pat_can_read_and_manage_user_skills_only_with_matching_scope(
    client: AsyncClient,
    anon_client: AsyncClient,
):
    read_pat = await _create_pat(client, ["skills:read"])
    read_headers = _pat_headers(read_pat["token"])
    assert (await anon_client.get("/api/v1/skills", headers=read_headers)).status_code == 200
    assert (
        await anon_client.put(
            "/api/v1/skills/not-present/enabled",
            headers=read_headers,
            json={"enabled": True},
        )
    ).status_code == 403

    write_pat = await _create_pat(client, ["skills:write"])
    write_headers = _pat_headers(write_pat["token"])
    assert (await anon_client.get("/api/v1/skills", headers=write_headers)).status_code == 403
    # The scope passes and the existing resource-hiding rule supplies the 404.
    assert (
        await anon_client.put(
            "/api/v1/skills/not-present/enabled",
            headers=write_headers,
            json={"enabled": True},
        )
    ).status_code == 404


async def test_pat_cannot_manage_credentials_or_use_admin_role(
    client: AsyncClient,
    admin_client: AsyncClient,
    anon_client: AsyncClient,
):
    user_pat = await _create_pat(client, ["conversations:read"])
    user_headers = _pat_headers(user_pat["token"])
    assert (await anon_client.get("/api/v1/auth/pats", headers=user_headers)).status_code == 401
    assert (
        await anon_client.patch(
            "/api/v1/auth/me",
            headers=user_headers,
            json={"display_name": "changed"},
        )
    ).status_code == 401

    admin_pat = await _create_pat(
        admin_client,
        ["conversations:read"],
        name="admin ordinary API",
    )
    admin_headers = _pat_headers(admin_pat["token"])
    assert (await anon_client.get("/api/v1/chat", headers=admin_headers)).status_code == 200
    assert (
        await anon_client.get("/api/v1/admin/users", headers=admin_headers)
    ).status_code == 401
    assert (
        await anon_client.get("/api/v1/departments", headers=admin_headers)
    ).status_code == 401


async def test_pat_tool_approval_supports_always_allow(
    client: AsyncClient,
    anon_client: AsyncClient,
    app,
    test_user: User,
    db_manager: DatabaseManager,
):
    conv_id = f"conv-{uuid.uuid4().hex}"
    message_id = f"msg-{uuid.uuid4().hex}"
    call_id = f"call-{uuid.uuid4().hex}"
    async with db_manager.session() as session:
        repo = ConversationRepository(session)
        await repo.create_conversation(conv_id, "approval", user_id=test_user.id)
        await repo.add_message(conv_id, message_id, "approve tool")

    store = app.dependency_overrides[get_runtime_store]()
    interrupt = _InterruptState(interrupt_data={
        "call_id": call_id,
        "tool": "sensitive_tool",
        "params": {"target": "example"},
    })
    store._interrupts[message_id] = interrupt

    pat = await _create_pat(client, ["tools:approve"])
    headers = _pat_headers(pat["token"])
    response = await anon_client.post(
        f"/api/v1/chat/{conv_id}/resume",
        headers=headers,
        json={
            "message_id": message_id,
            "call_id": call_id,
            "approved": True,
            "always_allow": True,
        },
    )
    assert response.status_code == 200
    assert interrupt.resume_data == {"approved": True, "always_allow": True}


async def test_expired_disabled_and_password_gated_pat_fail_closed(
    client: AsyncClient,
    anon_client: AsyncClient,
    test_user: User,
    db_manager: DatabaseManager,
):
    pat = await _create_pat(client, ["conversations:read"])
    headers = _pat_headers(pat["token"])

    async with db_manager.session() as session:
        row = await session.get(PersonalAccessToken, pat["id"])
        assert row is not None
        row.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()
    assert (await anon_client.get("/api/v1/auth/me", headers=headers)).status_code == 401

    gated = await _create_pat(client, ["conversations:read"], name="gated")
    gated_headers = _pat_headers(gated["token"])
    async with db_manager.session() as session:
        user = await session.get(User, test_user.id)
        assert user is not None
        user.must_change_password = True
        await session.commit()
    assert (await anon_client.get("/api/v1/auth/me", headers=gated_headers)).status_code == 200
    assert (await anon_client.get("/api/v1/chat", headers=gated_headers)).status_code == 403

    async with db_manager.session() as session:
        user = await session.get(User, test_user.id)
        assert user is not None
        user.must_change_password = False
        user.is_active = False
        await session.commit()
    assert (await anon_client.get("/api/v1/auth/me", headers=gated_headers)).status_code == 401
