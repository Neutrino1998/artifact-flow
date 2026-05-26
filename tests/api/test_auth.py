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

    async def test_unknown_username_still_runs_bcrypt(
        self, anon_client: AsyncClient, monkeypatch
    ):
        """ACC-05: 用户不存在也对固定假 hash 跑一次 verify_password(等时防枚举)。

        不做脆弱的墙钟断言 —— 用 spy 直接验证「verify_password 被调用一次,
        且 hash 参数 = DUMMY_PASSWORD_HASH」即证明两分支都过 bcrypt。
        """
        import api.routers.auth as auth_router

        calls: list[str] = []
        real = auth_router.verify_password

        def _spy(plain: str, hashed: str) -> bool:
            calls.append(hashed)
            return real(plain, hashed)

        monkeypatch.setattr(auth_router, "verify_password", _spy)

        resp = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "no-such-user-xyz", "password": "whatever1!"},
        )
        assert resp.status_code == 401
        assert calls == [auth_router.DUMMY_PASSWORD_HASH]

    async def test_login_inactive_user(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
        test_user: User,
        anon_client: AsyncClient,
    ):
        # Deactivate via admin
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_user.id}",
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
            "/api/v1/admin/users",
            json={
                "username": f"newuser-{uuid.uuid4().hex[:8]}",
                "password": "Newpass1234!",
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
            "/api/v1/admin/users",
            json={
                "username": test_user.username,
                "password": "Somepass1234!",
                "role": "user",
            },
        )
        assert resp.status_code == 409

    async def test_create_user_as_regular_user(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/admin/users",
            json={
                "username": "shouldfail",
                "password": "pass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 403

    async def test_create_user_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/admin/users",
            json={
                "username": "shouldfail",
                "password": "pass1234",
                "role": "user",
            },
        )
        assert resp.status_code == 401

    async def test_create_user_invalid_role(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={
                "username": f"badrole-{uuid.uuid4().hex[:8]}",
                "password": "Pass1234!",
                "role": "superuser",
            },
        )
        assert resp.status_code == 400

    async def test_list_users_as_admin(
        self, admin_client: AsyncClient, test_user: User, test_admin: User
    ):
        resp = await admin_client.get("/api/v1/admin/users")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 2
        assert len(body["users"]) >= 2

    async def test_list_users_pagination(
        self, admin_client: AsyncClient, test_user: User, test_admin: User
    ):
        resp0 = await admin_client.get("/api/v1/admin/users?limit=1&offset=0")
        assert resp0.status_code == 200
        assert len(resp0.json()["users"]) == 1

        resp1 = await admin_client.get("/api/v1/admin/users?limit=1&offset=1")
        assert resp1.status_code == 200
        assert len(resp1.json()["users"]) == 1

        # Different users on different pages
        assert resp0.json()["users"][0]["id"] != resp1.json()["users"][0]["id"]

    async def test_list_users_as_regular_user(self, client: AsyncClient):
        resp = await client.get("/api/v1/admin/users")
        assert resp.status_code == 403

    async def test_update_user_as_admin(
        self, admin_client: AsyncClient, test_user: User
    ):
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_user.id}",
            json={"display_name": "Updated Display"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Display"

    async def test_update_user_not_found(self, admin_client: AsyncClient):
        resp = await admin_client.put(
            "/api/v1/admin/users/nonexistent-id",
            json={"display_name": "x"},
        )
        assert resp.status_code == 404

    async def test_update_user_as_regular_user(
        self, client: AsyncClient, test_user: User
    ):
        resp = await client.put(
            f"/api/v1/admin/users/{test_user.id}",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 403


class TestUpdateMyProfile:

    async def test_update_own_display_name(self, client: AsyncClient, test_user: User):
        resp = await client.patch(
            "/api/v1/auth/me",
            json={"display_name": "Hello World"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Hello World"
        assert body["username"] == test_user.username
        assert body["id"] == test_user.id

    async def test_clear_display_name_with_empty_string(self, client: AsyncClient):
        # First set a value, then clear via empty string
        await client.patch("/api/v1/auth/me", json={"display_name": "Foo"})
        resp = await client.patch("/api/v1/auth/me", json={"display_name": ""})
        assert resp.status_code == 200
        assert resp.json()["display_name"] is None

    async def test_unauthenticated_rejected(self, anon_client: AsyncClient):
        resp = await anon_client.patch(
            "/api/v1/auth/me",
            json={"display_name": "x"},
        )
        assert resp.status_code == 401

    async def test_does_not_touch_role_or_active(
        self, client: AsyncClient, test_user: User, db_manager
    ):
        """schema 仅声明 display_name；其他字段被 Pydantic 默默忽略，不影响行。"""
        await client.patch(
            "/api/v1/auth/me",
            json={"display_name": "ok", "role": "admin", "is_active": False},
        )
        from sqlalchemy import select
        async with db_manager.session() as s:
            result = await s.execute(select(User).where(User.id == test_user.id))
            row = result.scalar_one()
            assert row.role == "user"  # unchanged
            assert row.is_active is True  # unchanged
            assert row.display_name == "ok"

    async def test_admin_can_use_endpoint_too(
        self, admin_client: AsyncClient, test_admin: User
    ):
        """与 PUT /users/{id} 的 self password lock 配合：admin 改自己 display_name 的官方路径。"""
        resp = await admin_client.patch(
            "/api/v1/auth/me",
            json={"display_name": "Captain Admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Captain Admin"


class TestChangeMyPassword:

    async def test_success_and_relogin(
        self,
        client: AsyncClient,
        anon_client: AsyncClient,
        test_user: User,
    ):
        resp = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "testpass", "new_password": "Newpass1234!"},
        )
        assert resp.status_code == 204

        # New password works
        ok = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "Newpass1234!"},
        )
        assert ok.status_code == 200

        # Old password no longer works
        fail = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert fail.status_code == 401

    async def test_wrong_current_password(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "wrongpass", "new_password": "Newpass1234!"},
        )
        assert resp.status_code == 400
        assert "current password" in resp.json()["detail"].lower()

    async def test_new_password_too_short(self, client: AsyncClient, test_user: User):
        resp = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "testpass", "new_password": "abc"},
        )
        assert resp.status_code == 422

    async def test_unauthenticated(self, anon_client: AsyncClient):
        resp = await anon_client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "x", "new_password": "abcd"},
        )
        assert resp.status_code == 401

    async def test_old_token_invalidated_after_change(
        self,
        client: AsyncClient,
        anon_client: AsyncClient,
        test_user: User,
    ):
        """改密前签发的 token 改密后应当 401（pwd_v 校验）"""
        # client fixture token was issued with pwd_v=0
        ok = await client.get("/api/v1/auth/me")
        assert ok.status_code == 200

        # Change password
        resp = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "testpass", "new_password": "Newpass1234!"},
        )
        assert resp.status_code == 204

        # Old token now rejected
        stale = await client.get("/api/v1/auth/me")
        assert stale.status_code == 401

    async def test_admin_password_reset_invalidates_user_token(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
        test_user: User,
    ):
        """admin 给用户重置密码也应吊销该用户旧 token"""
        ok = await client.get("/api/v1/auth/me")
        assert ok.status_code == 200

        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_user.id}",
            json={"password": "Reset1234!"},
        )
        assert resp.status_code == 200

        stale = await client.get("/api/v1/auth/me")
        assert stale.status_code == 401


class TestLongPasswordBcrypt:
    """ACC-04: >72 字节口令不应 500（bcrypt 5.0 会抛 ValueError;我们截到 72 字节
    + 全局 handler 兜底）。多字节口令在 72 字节边界被切断不影响 bcrypt。"""

    async def test_create_and_login_with_long_multibyte_password(
        self, admin_client: AsyncClient, anon_client: AsyncClient
    ):
        uname = f"longpw-{uuid.uuid4().hex[:8]}"
        long_pw = "Aa1!" + "中" * 30  # 94 字节 / 34 字符,强度达标且超 72 字节

        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": uname, "password": long_pw, "role": "user"},
        )
        assert resp.status_code == 200  # 不是 500

        # 完整明文登录成功(hash 与 verify 用同样的 72 字节截断)
        ok = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": uname, "password": long_pw},
        )
        assert ok.status_code == 200


class TestUsernameValidation:

    async def test_create_user_rejects_space(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": "bad name", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 422

    async def test_create_user_rejects_chinese(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": "张三", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 422

    async def test_create_user_rejects_too_short(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": "a", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 422

    async def test_create_user_accepts_special_chars(self, admin_client: AsyncClient):
        resp = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": "a.b_c-d", "password": "Pass1234!", "role": "user"},
        )
        assert resp.status_code == 200
        assert resp.json()["username"] == "a.b_c-d"
