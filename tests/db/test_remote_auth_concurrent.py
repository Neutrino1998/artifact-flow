"""Concurrent first-login identity uniqueness on independent DB sessions."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from core.management.department_manager import DepartmentManager
from core.management.remote_auth_manager import RemoteAuthManager
from core.security.remote_bearer_config import RemoteBearerConfig
from core.security.remote_bearer_userinfo import NormalizedRemoteIdentity
from core.security.sso_state import InMemorySsoStateStore
from db.database import DatabaseManager
from db.models import User
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository


@pytest.fixture
async def file_db(tmp_path):
    manager = DatabaseManager(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'remote-auth.db'}"
    )
    await manager.initialize()
    yield manager
    await manager.close()


def _config() -> RemoteBearerConfig:
    return RemoteBearerConfig.model_validate(
        {
            "version": 1,
            "enabled": True,
            "provider": {"id": "enterprise_sso", "display_name": "SSO"},
            "login": {
                "url": "https://identity.example/login",
                "callback_url": "https://app.example/callback",
                "return_param": "entryPath",
                "token_param": "authorization_key",
            },
            "userinfo": {
                "url": "https://identity.example/info",
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


class _BarrierClient:
    def __init__(self, barrier: asyncio.Barrier):
        self._barrier = barrier

    async def fetch(self, _token: str) -> NormalizedRemoteIdentity:
        await self._barrier.wait()
        return NormalizedRemoteIdentity(
            subject="same-subject",
            username="same-user",
            display_name="Same User",
            enabled=True,
            department_path=None,
        )


async def test_concurrent_first_login_creates_one_remote_user(file_db):
    barrier = asyncio.Barrier(2)
    provider = _config()

    async def login_once() -> str:
        async with file_db.session() as session:
            state_store = InMemorySsoStateStore()
            manager = RemoteAuthManager(
                provider,
                state_store,
                _BarrierClient(barrier),
                UserRepository(session),
                DepartmentManager(DepartmentRepository(session)),
            )
            _url, issued = await manager.start()
            result = await manager.exchange(
                state=issued.state,
                browser_binding=issued.browser_binding,
                upstream_token="opaque",
            )
            return result["profile"]["id"]

    ids = await asyncio.gather(login_once(), login_once())
    assert ids[0] == ids[1]

    async with file_db.session() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(User)
                .where(
                    User.auth_provider == "enterprise_sso",
                    User.auth_subject == "same-subject",
                )
            )
        ).scalar_one()
        assert count == 1
