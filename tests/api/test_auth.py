"""
Auth API integration tests.

Covers login, /me endpoint, and admin user CRUD.
"""

import uuid

import pytest
from httpx import AsyncClient

from db.models import User
from repositories.user_repo import UserRepository


class TestLogin:

    async def test_login_success(self, anon_client: AsyncClient, test_user: User):
        resp = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["user"]["username"] == "testuser"

    async def test_login_wrong_password(self, anon_client: AsyncClient, test_user: User):
        resp = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "wrongpass"},
        )
        assert resp.status_code == 401

    async def test_login_unknown_username(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert resp.status_code == 401

    async def test_login_inactive_user(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
        test_user: User,
        anon_client: AsyncClient,
    ):
        # Deactivate via admin
        resp = await admin_client.put(
            f"/api/v1/auth/users/{test_user.id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200

        # Try login as deactivated user
        resp = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert resp.status_code == 401


class TestMe:

    async def test_get_me_authenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "testuser"
        assert body["role"] == "user"

    async def test_get_me_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_get_me_invalid_token(self, anon_client: AsyncClient):
        resp = await anon_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestAdminCRUD:

    async def test_create_user_as_admin(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/auth/users",
            json={
                "username": f"newuser-{uuid.uuid4().hex[:8]}",
                "password": "newpass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "user"
        assert body["is_active"] is True

    async def test_create_user_duplicate_username(
        self, admin_client: AsyncClient, test_user: User
    ):
        resp = await admin_client.post(
            "/api/v1/auth/users",
            json={
                "username": test_user.username,
                "password": "somepass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 409

    async def test_create_user_as_regular_user(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/users",
            json={
                "username": "shouldfail",
                "password": "pass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 403

    async def test_create_user_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/auth/users",
            json={
                "username": "shouldfail",
                "password": "pass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 401

    async def test_create_user_invalid_role(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/auth/users",
            json={
                "username": f"badrole-{uuid.uuid4().hex[:8]}",
                "password": "pass1234",
                "role": "superuser",
            },
        )
        assert resp.status_code == 400

    async def test_list_users_as_admin(
        self, admin_client: AsyncClient, test_user: User, test_admin: User
    ):
        resp = await admin_client.get("/api/v1/auth/users")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        assert len(body["users"]) >= 2

    async def test_list_users_pagination(
        self, admin_client: AsyncClient, test_user: User, test_admin: User
    ):
        resp0 = await admin_client.get("/api/v1/auth/users?limit=1&offset=0")
        assert resp0.status_code == 200
        assert len(resp0.json()["users"]) == 1

        resp1 = await admin_client.get("/api/v1/auth/users?limit=1&offset=1")
        assert resp1.status_code == 200
        assert len(resp1.json()["users"]) == 1

        # Different users on different pages
        assert resp0.json()["users"][0]["id"] != resp1.json()["users"][0]["id"]

    async def test_list_users_as_regular_user(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/users")
        assert resp.status_code == 403

    async def test_update_user_as_admin(
        self, admin_client: AsyncClient, test_user: User
    ):
        resp = await admin_client.put(
            f"/api/v1/auth/users/{test_user.id}",
            json={"display_name": "Updated Display"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Display"

    async def test_update_user_not_found(self, admin_client: AsyncClient):
        resp = await admin_client.put(
            "/api/v1/auth/users/nonexistent-id",
            json={"display_name": "x"},
        )
        assert resp.status_code == 404

    async def test_update_user_as_regular_user(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.put(
            f"/api/v1/auth/users/{test_user.id}",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 403
