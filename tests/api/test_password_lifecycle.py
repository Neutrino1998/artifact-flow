"""门类三 C6: 强制改密闸门 + 周期到期 + 不重用查重 + 建号/重置强制改密。

覆盖:
- must_change_password 闸门:置 True 时除 GET /me、POST /me/password 外 403;改密后解除
- 90/180 天到期:登录时口令超龄 → 标记强制改密
- 不重用:新口令 = 当前口令 → 400(PASSWORD_HISTORY_COUNT=1 即「≠ 当前」)
- admin 建号 / admin 重置 → 该用户首次登录强制改密
- CSV 导入 → 见 test_user_bulk_import.py::test_generated_temp_password_*
"""

import uuid
from datetime import timedelta

from httpx import AsyncClient

from api.services.auth import hash_password
from db.models import User
from repositories.user_repo import UserRepository
from utils.time import utc_now


class TestMustChangeGate:
    async def test_gate_blocks_until_changed(
        self, client: AsyncClient, test_user: User, db_manager
    ):
        # 正常态:受保护端点可用
        assert (await client.patch("/api/v1/auth/me", json={"display_name": "ok"})).status_code == 200

        # 置 must_change_password(模拟 admin 重置 / 到期)
        async with db_manager.session() as s:
            u = await s.get(User, test_user.id)
            u.must_change_password = True
            await s.commit()

        # GET /me 豁免 —— 仍可,且回带标志
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["must_change_password"] is True

        # 其他受保护端点(含 PATCH /me)被 403
        blocked = await client.patch("/api/v1/auth/me", json={"display_name": "nope"})
        assert blocked.status_code == 403
        assert "change" in blocked.json()["detail"].lower()

        # POST /me/password 豁免 —— 改密成功(清标志 + pwd_v++)
        changed = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "testpass", "new_password": "Brandnew1!"},
        )
        assert changed.status_code == 204


class TestPasswordExpiry:
    async def test_expired_password_forces_change_on_login(
        self, anon_client: AsyncClient, db_session
    ):
        repo = UserRepository(db_session)
        old = User(
            id=f"user-{uuid.uuid4().hex}",
            username="oldpw",
            hashed_password=hash_password("Valid1!aa"),
            role="user",
            is_active=True,
            must_change_password=False,
            password_changed_at=utc_now() - timedelta(days=999),  # 远超 180 天
        )
        await repo.add(old)

        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "oldpw", "password": "Valid1!aa"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True

    async def test_fresh_password_not_expired(
        self, anon_client: AsyncClient, db_session
    ):
        repo = UserRepository(db_session)
        fresh = User(
            id=f"user-{uuid.uuid4().hex}",
            username="freshpw",
            hashed_password=hash_password("Valid1!aa"),
            role="user",
            is_active=True,
            must_change_password=False,
            password_changed_at=utc_now(),
        )
        await repo.add(fresh)

        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "freshpw", "password": "Valid1!aa"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is False


class TestNoReuse:
    async def test_change_to_current_password_rejected(
        self, client: AsyncClient, anon_client: AsyncClient, test_user: User
    ):
        # 先改成强口令 A(testpass 是 fixture 弱口令,绕过了策略)
        r = await client.post(
            "/api/v1/auth/me/password",
            json={"current_password": "testpass", "new_password": "Strong1!aa"},
        )
        assert r.status_code == 204

        # pwd_v++ 使旧 token 失效 → 重新登录拿新 token
        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "Strong1!aa"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        # 把口令改成「当前口令」(同一个)→ 400 不重用(强度先过,reuse 命中)
        reuse = await anon_client.post(
            "/api/v1/auth/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "Strong1!aa", "new_password": "Strong1!aa"},
        )
        assert reuse.status_code == 400
        assert "最近" in reuse.json()["detail"]


class TestAdminForcesChange:
    async def test_admin_created_user_must_change(
        self, admin_client: AsyncClient, anon_client: AsyncClient
    ):
        uname = f"freshu-{uuid.uuid4().hex[:8]}"
        r = await admin_client.post(
            "/api/v1/admin/users",
            json={"username": uname, "password": "Created1!", "role": "user"},
        )
        assert r.status_code == 200
        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": uname, "password": "Created1!"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True

    async def test_admin_reset_forces_change(
        self, admin_client: AsyncClient, anon_client: AsyncClient, test_user: User
    ):
        r = await admin_client.put(
            f"/api/v1/admin/users/{test_user.id}",
            json={"password": "Reset1!aa"},
        )
        assert r.status_code == 200
        login = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "Reset1!aa"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["must_change_password"] is True
