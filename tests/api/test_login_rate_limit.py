"""ACC-01: 登录频控 —— per-username + per-IP 失败计数,超阈临时锁定。

每个 test 拿到全新 InMemory 频控器(见 tests/api/conftest.py 的 override),
计数不跨 test 泄漏。后端只认 X-Real-IP(不认可伪造的 XFF,见 _client_ip),
用唯一 X-Real-IP 可把 per-IP 锁排除,单独验 per-username 行为。
"""

import pytest
from httpx import AsyncClient

from api.services.login_rate_limiter import RedisLoginRateLimiter
from config import config
from db.models import User


class _FakeRedis:
    def __init__(self, retry_pttl):
        self.retry_pttl = retry_pttl
        self._script_count = 0

    def register_script(self, _script):
        self._script_count += 1
        script_index = self._script_count

        async def run(*, keys, args):
            assert len(keys) == 1
            assert args
            if script_index == 2:
                return self.retry_pttl
            return 1

        return run


class TestRedisLoginRateLimiter:
    @pytest.mark.parametrize(
        ("pttl", "expected"),
        [
            (None, None),
            (1001, 2),
            (999, 1),
            (0, 1),
            (-2, None),
            (-1, config.LOGIN_FAILURE_WINDOW_SEC),
        ],
    )
    async def test_retry_after_maps_pttl_boundaries(self, pttl, expected):
        limiter = RedisLoginRateLimiter(
            _FakeRedis(pttl),
            max_failures=config.LOGIN_MAX_FAILURES,
            window_sec=config.LOGIN_FAILURE_WINDOW_SEC,
        )

        assert await limiter.retry_after("user:alice") == expected


class TestLoginRateLimit:
    async def test_lockout_after_max_failures(
        self, anon_client: AsyncClient, test_user: User
    ):
        # 连续 max 次错密码,每次 401
        for _ in range(config.LOGIN_MAX_FAILURES):
            r = await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "wrong-pw-1!"},
            )
            assert r.status_code == 401

        # 超阈 → 429,连正确密码也拒(锁定本身就是目的)
        r = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
        )
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert "15 minutes" in r.json()["detail"]

    async def test_ip_lock_blocks_other_username(
        self, anon_client: AsyncClient, test_user: User
    ):
        # 同 IP 打满失败次数(默认 ASGITransport 共享同一 client IP)
        for _ in range(config.LOGIN_MAX_FAILURES):
            await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "wrong-pw-1!"},
            )
        # 同 IP 换个根本不存在的用户名也被 per-IP 锁挡下
        r = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "someone-else", "password": "whatever1!"},
        )
        assert r.status_code == 429

    async def test_success_resets_username_counter(
        self, anon_client: AsyncClient, test_user: User
    ):
        # 每次请求用唯一 X-Real-IP → per-IP 锁不触发,单独验 per-username 计数。
        # (后端只信 X-Real-IP,不信 XFF —— 见 _client_ip。)
        # max-1 次失败(未到锁定线)
        for i in range(config.LOGIN_MAX_FAILURES - 1):
            r = await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "wrong-pw-1!"},
                headers={"X-Real-IP": f"10.0.0.{i}"},
            )
            assert r.status_code == 401

        # 成功登录 → 重置 username 计数
        r = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
            headers={"X-Real-IP": "10.0.1.1"},
        )
        assert r.status_code == 200

        # 重置后再来 max-1 次失败应全 401 —— 若未重置,计数从 max-1 续起,
        # 第二次失败就会 429。
        for i in range(config.LOGIN_MAX_FAILURES - 1):
            r = await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "wrong-pw-1!"},
                headers={"X-Real-IP": f"10.0.2.{i}"},
            )
            assert r.status_code == 401

    async def test_forged_xff_does_not_bypass_ip_limit(
        self, anon_client: AsyncClient, test_user: User
    ):
        """P1 回归:每次换不同 X-Forwarded-For **不应**绕过 per-IP 锁 —— 后端只认
        X-Real-IP / client.host,不认可伪造的 XFF。所有请求实际共享同一 client.host
        → IP 计数照样打满。"""
        for i in range(config.LOGIN_MAX_FAILURES):
            r = await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "wrong-pw-1!"},
                headers={"X-Forwarded-For": f"9.9.9.{i}"},
            )
            assert r.status_code == 401
        # 再换个 XFF 也已被锁
        r = await anon_client.post(
            "/api/v1/auth/login",
            json={"username": "testuser", "password": "testpass"},
            headers={"X-Forwarded-For": "9.9.9.250"},
        )
        assert r.status_code == 429

    async def test_disabled_user_correct_password_not_counted(
        self, admin_client: AsyncClient, anon_client: AsyncClient, test_user: User
    ):
        # 禁用账号
        resp = await admin_client.put(
            f"/api/v1/admin/users/{test_user.id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200

        # 用「正确密码」登录被禁用账号 → 401 disabled,但这不是撞库信号,
        # 不应累计;多次也不会 429。
        for _ in range(config.LOGIN_MAX_FAILURES + 2):
            r = await anon_client.post(
                "/api/v1/auth/login",
                json={"username": "testuser", "password": "testpass"},
            )
            assert r.status_code == 401  # disabled,而非 429
