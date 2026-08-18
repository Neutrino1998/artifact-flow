"""Anonymous SSO handshake and exchange integration tests."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

from httpx import AsyncClient

from api.dependencies import get_remote_auth_manager, get_remote_bearer_config
from core.management.department_manager import DepartmentManager
from core.management.remote_auth_manager import RemoteAuthManager
from core.security.remote_bearer_config import RemoteBearerConfig
from core.security.remote_bearer_userinfo import (
    NormalizedRemoteIdentity,
    RemoteBearerProtocolError,
)
from core.security.sso_state import InMemorySsoStateStore
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository


def _config() -> RemoteBearerConfig:
    return RemoteBearerConfig.model_validate(
        {
            "version": 1,
            "enabled": True,
            "provider": {
                "id": "enterprise_sso",
                "display_name": "Enterprise SSO",
            },
            "login": {
                "url": "https://identity.example/login",
                "callback_url": "http://testserver/auth/sso/callback",
                "return_param": "entryPath",
                "token_param": "authorization_key",
                "state_ttl_seconds": 300,
            },
            "userinfo": {
                "url": "http://identity.internal/auth/info",
                "allow_insecure_http": True,
                "fields": {
                    "subject": "user.id",
                    "username": "user.username",
                    "display_name": "user.name",
                    "enabled": "user.enabled",
                    "department_path": "user.path",
                    "department_leaf": "user.dept.name",
                },
            },
        }
    )


class _Client:
    def __init__(self):
        self.tokens: list[str] = []

    async def fetch(self, token: str) -> NormalizedRemoteIdentity:
        self.tokens.append(token)
        return NormalizedRemoteIdentity(
            subject="upstream-42",
            username="remote-user",
            display_name="Remote User",
            enabled=True,
            department_path=None,
        )


class _ProtocolFailureClient:
    async def fetch(self, _token: str) -> NormalizedRemoteIdentity:
        raise RemoteBearerProtocolError("userinfo response is not valid JSON")


def _state_from_authorization_url(url: str) -> str:
    login_query = parse_qs(urlsplit(url).query)
    callback = login_query["entryPath"][0]
    return parse_qs(urlsplit(callback).query)["af_sso_state"][0]


async def test_enabled_config_start_exchange_and_replay_rejection(
    app,
    anon_client: AsyncClient,
    admin_client: AsyncClient,
    db_session,
):
    provider = _config()
    upstream = _Client()
    manager = RemoteAuthManager(
        provider,
        InMemorySsoStateStore(),
        upstream,
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )
    app.dependency_overrides[get_remote_bearer_config] = lambda: provider
    app.dependency_overrides[get_remote_auth_manager] = lambda: manager

    public = await anon_client.get("/api/v1/auth/config")
    assert public.status_code == 200
    assert public.json()["sso"] == {
        "enabled": True,
        "provider_id": "enterprise_sso",
        "display_name": "Enterprise SSO",
        "token_param": "authorization_key",
    }

    start = await anon_client.post("/api/v1/auth/sso/start")
    assert start.status_code == 200
    assert start.headers["cache-control"] == "no-store"
    state = _state_from_authorization_url(start.json()["authorization_url"])
    assert "af_sso_binding" in anon_client.cookies

    secret = "upstream-secret-that-must-not-be-returned"
    exchange = await anon_client.post(
        "/api/v1/auth/sso/exchange",
        json={"state": state, "upstream_token": secret},
    )
    assert exchange.status_code == 200
    body = exchange.json()
    assert body["expires_in"] == 8 * 60 * 60
    assert body["user"]["auth_provider"] == "enterprise_sso"
    assert body["user"]["can_change_password"] is False
    assert secret not in exchange.text
    assert upstream.tokens == [secret]

    replay = await anon_client.post(
        "/api/v1/auth/sso/exchange",
        json={"state": state, "upstream_token": secret},
    )
    assert replay.status_code == 401
    assert upstream.tokens == [secret]

    remote_token = body["access_token"]
    password_change = await anon_client.post(
        "/api/v1/auth/me/password",
        headers={"Authorization": f"Bearer {remote_token}"},
        json={"current_password": "irrelevant", "new_password": "Strong1!pass"},
    )
    assert password_change.status_code == 403

    admin_reset = await admin_client.put(
        f"/api/v1/admin/users/{body['user']['id']}",
        json={"password": "AdminReset1!pass"},
    )
    assert admin_reset.status_code == 400
    assert "unavailable" in admin_reset.json()["detail"].lower()


async def test_invalid_exchange_shape_does_not_echo_token(
    app,
    anon_client: AsyncClient,
    db_session,
):
    provider = _config()
    manager = RemoteAuthManager(
        provider,
        InMemorySsoStateStore(),
        _Client(),
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )
    app.dependency_overrides[get_remote_auth_manager] = lambda: manager

    secret = "token-visible-only-in-request"
    response = await anon_client.post(
        "/api/v1/auth/sso/exchange",
        json={"state": [], "upstream_token": secret},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid exchange request"
    assert secret not in response.text


async def test_upstream_protocol_5xx_is_logged_without_bearer(
    app,
    anon_client: AsyncClient,
    db_session,
    caplog,
):
    provider = _config()
    manager = RemoteAuthManager(
        provider,
        InMemorySsoStateStore(),
        _ProtocolFailureClient(),
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )
    app.dependency_overrides[get_remote_auth_manager] = lambda: manager
    start = await anon_client.post("/api/v1/auth/sso/start")
    state = _state_from_authorization_url(start.json()["authorization_url"])
    secret = "bearer-must-never-reach-logs"

    with caplog.at_level(logging.ERROR, logger="ArtifactFlow"):
        response = await anon_client.post(
            "/api/v1/auth/sso/exchange",
            json={"state": state, "upstream_token": secret},
        )

    assert response.status_code == 502
    assert response.headers.get("x-request-id")
    assert "SSO userinfo protocol failure" in caplog.text
    assert secret not in caplog.text
    assert secret not in response.text
