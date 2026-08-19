"""Remote bearer provider config, normalization, and one-time state contracts."""

from __future__ import annotations

import pytest
import httpx
from sqlalchemy import select

from core.management.department_manager import DepartmentManager
from core.management.remote_auth_manager import (
    RemoteAuthIdentityDisabledError,
    RemoteAuthManager,
)
from core.security.remote_bearer_config import (
    RemoteBearerConfig,
    RemoteBearerConfigError,
    load_remote_bearer_config,
)
from core.security.remote_bearer_userinfo import (
    NormalizedRemoteIdentity,
    RemoteBearerCredentialsRejected,
    RemoteBearerProtocolError,
    RemoteBearerUpstreamUnavailable,
    RemoteBearerUserInfoClient,
    normalize_remote_userinfo,
)
from core.security.sso_state import (
    InMemorySsoStateStore,
    RedisSsoStateStore,
    SsoStateCapacityError,
)
from db.models import User
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository


def _enabled_config(**userinfo_overrides) -> RemoteBearerConfig:
    userinfo = {
        "url": "https://identity.example/auth/info",
        "fields": {
            "subject": "user.id",
            "username": "user.username",
            "display_name": "user.name",
            "enabled": "user.enabled",
            "department_path": "user.superiorDeptName",
            "department_leaf": "user.dept.name",
        },
    }
    userinfo.update(userinfo_overrides)
    return RemoteBearerConfig.model_validate(
        {
            "version": 1,
            "enabled": True,
            "provider": {
                "id": "enterprise_sso",
                "display_name": "Enterprise SSO",
                "type": "remote_bearer_userinfo",
            },
            "login": {
                "url": "https://identity.example/login",
                "callback_url": "https://app.example/auth/sso/callback",
                "return_param": "entryPath",
                "token_param": "authorization_key",
            },
            "userinfo": userinfo,
        }
    )


def _payload(**user_overrides):
    user = {
        "id": 1234,
        "username": "alice",
        "name": "Alice",
        "enabled": True,
        "superiorDeptName": "Company / Platform / Runtime",
        "dept": {"name": "Runtime"},
    }
    user.update(user_overrides)
    return {"user": user}


def test_missing_provider_file_is_disabled(tmp_path):
    assert load_remote_bearer_config(tmp_path / "missing.yaml").enabled is False


def test_enabled_http_urls_require_explicit_acknowledgement():
    raw = _enabled_config().model_dump()
    raw["userinfo"]["url"] = "http://identity.internal/auth/info"
    with pytest.raises(ValueError, match="allow_insecure_http"):
        RemoteBearerConfig.model_validate(raw)

    raw["userinfo"]["allow_insecure_http"] = True
    assert RemoteBearerConfig.model_validate(raw).enabled is True


def test_enabled_provider_requires_every_contract_section():
    with pytest.raises(ValueError, match="requires provider, login, and userinfo"):
        RemoteBearerConfig.model_validate({"version": 1, "enabled": True})


@pytest.mark.parametrize(
    "url",
    [
        "https://exa mple.com/info",
        "https://-invalid.example/info",
        "https://identity.example/%ZZ",
    ],
)
def test_enabled_provider_rejects_malformed_urls_at_startup(url):
    raw = _enabled_config().model_dump()
    raw["userinfo"]["url"] = url
    with pytest.raises(ValueError):
        RemoteBearerConfig.model_validate(raw)


def test_enabled_provider_rejects_state_parameter_as_upstream_token_name():
    raw = _enabled_config().model_dump()
    raw["login"]["token_param"] = "af_sso_state"
    with pytest.raises(ValueError, match="reserved name"):
        RemoteBearerConfig.model_validate(raw)


def test_enabled_provider_accepts_valid_idna_hostname():
    raw = _enabled_config().model_dump()
    raw["userinfo"]["url"] = "https://例子.测试/info"
    assert RemoteBearerConfig.model_validate(raw).enabled is True


def test_loader_rejects_expression_like_field_path(tmp_path):
    path = tmp_path / "provider.yaml"
    path.write_text(
        """
version: 1
enabled: true
provider: {id: enterprise_sso, display_name: SSO}
login:
  url: https://identity.example/login
  callback_url: https://app.example/callback
  return_param: entryPath
  token_param: token
userinfo:
  url: https://identity.example/info
  fields:
    subject: users[0].id
    username: user.username
    display_name: user.name
    enabled: user.enabled
    department_path: user.path
    department_leaf: user.dept.name
""",
        encoding="utf-8",
    )
    with pytest.raises(RemoteBearerConfigError, match="dot-separated object path"):
        load_remote_bearer_config(path)


def test_normalize_department_path_and_integer_subject():
    identity = normalize_remote_userinfo(_payload(), _enabled_config())
    assert identity.subject == "1234"
    assert identity.username == "alice"
    assert identity.department_path == ("Company", "Platform", "Runtime")


@pytest.mark.parametrize(
    "path,leaf",
    [
        (None, "Runtime"),
        ("Company / Runtime", None),
        ("Company // Runtime", "Runtime"),
        ("Company / Runtime", "Other"),
    ],
)
def test_normalize_rejects_inconsistent_department_shape(path, leaf):
    payload = _payload(superiorDeptName=path, dept={"name": leaf})
    with pytest.raises(RemoteBearerProtocolError):
        normalize_remote_userinfo(payload, _enabled_config())


def test_normalize_accepts_explicit_no_department():
    identity = normalize_remote_userinfo(
        _payload(superiorDeptName="", dept={"name": ""}), _enabled_config()
    )
    assert identity.department_path is None


def test_normalize_rejects_subject_with_surrounding_whitespace():
    with pytest.raises(RemoteBearerProtocolError, match="whitespace"):
        normalize_remote_userinfo(_payload(id=" 1234 "), _enabled_config())


def test_normalize_rejects_missing_required_field():
    payload = _payload()
    payload["user"].pop("enabled")
    with pytest.raises(RemoteBearerProtocolError, match="missing required field enabled"):
        normalize_remote_userinfo(payload, _enabled_config())


async def test_inmemory_state_is_browser_bound_and_one_time():
    store = InMemorySsoStateStore(max_pending=2, clock=lambda: 100.0)
    issued = await store.issue(60)

    assert await store.consume(issued.state, "wrong-browser") is False
    assert await store.consume(issued.state, issued.browser_binding) is True
    assert await store.consume(issued.state, issued.browser_binding) is False


async def test_inmemory_state_expires():
    clock = iter([100.0, 161.0])
    store = InMemorySsoStateStore(clock=lambda: next(clock))
    issued = await store.issue(60)
    assert await store.consume(issued.state, issued.browser_binding) is False


async def test_inmemory_state_enforces_hard_pending_capacity():
    store = InMemorySsoStateStore(max_pending=1, clock=lambda: 100.0)
    await store.issue(60)

    with pytest.raises(SsoStateCapacityError):
        await store.issue(60)


class _FakeStateRedis:
    def __init__(self):
        self.records: dict[str, set[str]] = {}
        self._script_count = 0

    def register_script(self, _script):
        self._script_count += 1
        script_index = self._script_count

        async def run(*, keys, args):
            assert len(keys) == 1
            key = keys[0]
            records = self.records.setdefault(key, set())
            member = args[0]
            if script_index == 1:
                assert int(args[2]) > 0
                if len(records) >= int(args[1]):
                    return -1
                if member in records:
                    return 0
                records.add(member)
                return 1
            if member not in records:
                return 0
            records.remove(member)
            return 1

        return run


async def test_redis_state_is_single_key_bound_and_one_time():
    redis = _FakeStateRedis()
    store = RedisSsoStateStore(redis, max_pending=2, key_prefix="tenant")
    issued = await store.issue(60)
    key = next(iter(redis.records))
    assert issued.state not in key
    assert key == "tenant:sso:state:pending"

    assert await store.consume(issued.state, "wrong") is False
    assert await store.consume(issued.state, issued.browser_binding) is True
    assert await store.consume(issued.state, issued.browser_binding) is False


async def test_redis_state_enforces_hard_pending_capacity():
    redis = _FakeStateRedis()
    store = RedisSsoStateStore(redis, max_pending=1)
    await store.issue(60)

    with pytest.raises(SsoStateCapacityError):
        await store.issue(60)


async def _mock_userinfo_client(handler) -> RemoteBearerUserInfoClient:
    client = RemoteBearerUserInfoClient(_enabled_config(), max_connections=2)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_userinfo_client_applies_explicit_connection_bound():
    client = RemoteBearerUserInfoClient(_enabled_config(), max_connections=7)
    try:
        assert client._client._transport._pool._max_connections == 7
    finally:
        await client.close()


async def test_userinfo_client_sends_bearer_without_following_redirects():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/info"})

    client = await _mock_userinfo_client(handler)
    try:
        with pytest.raises(RemoteBearerProtocolError, match="unexpected HTTP 302"):
            await client.fetch("Bearer opaque-token")
    finally:
        await client.close()

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer opaque-token"


async def test_userinfo_client_rejects_unsafe_header_token_before_network():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_payload())

    client = await _mock_userinfo_client(handler)
    secret = "opaque\tsecret"
    try:
        with pytest.raises(RemoteBearerCredentialsRejected) as exc_info:
            await client.fetch(secret)
    finally:
        await client.close()

    assert called is False
    assert secret not in str(exc_info.value)


async def test_userinfo_client_normalizes_success_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload())

    client = await _mock_userinfo_client(handler)
    try:
        identity = await client.fetch("opaque-token")
    finally:
        await client.close()
    assert identity.subject == "1234"
    assert identity.department_path == ("Company", "Platform", "Runtime")


@pytest.mark.parametrize("status", [401, 403])
async def test_userinfo_client_maps_upstream_auth_rejection(status):
    client = await _mock_userinfo_client(
        lambda request: httpx.Response(status, request=request)
    )
    try:
        with pytest.raises(RemoteBearerCredentialsRejected):
            await client.fetch("opaque-token")
    finally:
        await client.close()


async def test_userinfo_client_maps_timeout_without_token_in_diagnostic():
    secret = "opaque-token-value"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    client = await _mock_userinfo_client(handler)
    try:
        with pytest.raises(RemoteBearerUpstreamUnavailable) as exc_info:
            await client.fetch(secret)
    finally:
        await client.close()
    assert secret not in str(exc_info.value)


async def test_userinfo_client_maps_upstream_5xx_to_unavailable():
    client = await _mock_userinfo_client(
        lambda request: httpx.Response(503, request=request)
    )
    try:
        with pytest.raises(RemoteBearerUpstreamUnavailable, match="HTTP 503"):
            await client.fetch("opaque-token")
    finally:
        await client.close()


async def test_userinfo_client_rejects_non_json_and_oversized_responses():
    responses = iter(
        [
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, content=b"x", headers={"Content-Length": "1048577"}),
        ]
    )
    client = await _mock_userinfo_client(lambda _request: next(responses))
    try:
        with pytest.raises(RemoteBearerProtocolError, match="not valid JSON"):
            await client.fetch("opaque-token")
        with pytest.raises(RemoteBearerProtocolError, match="too large"):
            await client.fetch("opaque-token")
    finally:
        await client.close()


class _IdentityClient:
    def __init__(self, identity: NormalizedRemoteIdentity):
        self.identity = identity
        self.tokens: list[str] = []

    async def fetch(self, token: str) -> NormalizedRemoteIdentity:
        self.tokens.append(token)
        return self.identity


def _identity(
    *,
    subject: str = "remote-1",
    username: str = "alice",
    enabled: bool = True,
    path: tuple[str, ...] | None = ("Company", "Platform"),
) -> NormalizedRemoteIdentity:
    return NormalizedRemoteIdentity(
        subject=subject,
        username=username,
        display_name="Alice",
        enabled=enabled,
        department_path=path,
    )


async def _exchange(manager: RemoteAuthManager) -> dict:
    _url, issued = await manager.start()
    return await manager.exchange(
        state=issued.state,
        browser_binding=issued.browser_binding,
        upstream_token="secret-upstream-token",
    )


async def test_jit_reuses_provider_subject_and_resolves_department(db_session):
    config = _enabled_config()
    client = _IdentityClient(_identity())
    manager = RemoteAuthManager(
        config,
        InMemorySsoStateStore(),
        client,
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )

    first = await _exchange(manager)
    second = await _exchange(manager)

    assert first["profile"]["id"] == second["profile"]["id"]
    assert first["profile"]["id"] != "remote-1"
    assert first["profile"]["department_path"] == ["Company", "Platform"]
    rows = (
        await db_session.execute(
            select(User).where(User.auth_provider == "enterprise_sso")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].auth_subject == "remote-1"
    assert rows[0].hashed_password is None
    assert client.tokens == ["secret-upstream-token", "secret-upstream-token"]


async def test_jit_allows_same_username_for_distinct_subjects(db_session):
    config = _enabled_config()
    client = _IdentityClient(_identity(subject="subject-a", username="shared"))
    manager = RemoteAuthManager(
        config,
        InMemorySsoStateStore(),
        client,
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )
    first = await _exchange(manager)
    client.identity = _identity(subject="subject-b", username="shared")
    second = await _exchange(manager)

    assert first["profile"]["id"] != second["profile"]["id"]


async def test_explicit_no_department_clears_previous_assignment(db_session):
    config = _enabled_config()
    client = _IdentityClient(_identity(path=("Company", "Platform")))
    manager = RemoteAuthManager(
        config,
        InMemorySsoStateStore(),
        client,
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )
    first = await _exchange(manager)
    client.identity = _identity(path=None)
    second = await _exchange(manager)

    assert second["profile"]["id"] == first["profile"]["id"]
    assert second["profile"]["department_path"] is None
    user = await db_session.get(User, first["profile"]["id"])
    assert user.department_id is None


async def test_disabled_upstream_identity_is_not_provisioned(db_session):
    config = _enabled_config()
    manager = RemoteAuthManager(
        config,
        InMemorySsoStateStore(),
        _IdentityClient(_identity(enabled=False)),
        UserRepository(db_session),
        DepartmentManager(DepartmentRepository(db_session)),
    )

    with pytest.raises(RemoteAuthIdentityDisabledError):
        await _exchange(manager)
    rows = (
        await db_session.execute(
            select(User).where(User.auth_provider == "enterprise_sso")
        )
    ).scalars().all()
    assert rows == []


async def test_locally_disabled_remote_identity_is_not_reenabled(db_session):
    config = _enabled_config()
    client = _IdentityClient(_identity())
    users = UserRepository(db_session)
    manager = RemoteAuthManager(
        config,
        InMemorySsoStateStore(),
        client,
        users,
        DepartmentManager(DepartmentRepository(db_session)),
    )
    first = await _exchange(manager)
    user = await users.get_by_id(first["profile"]["id"])
    user.is_active = False
    await users.save_user(user)

    with pytest.raises(RemoteAuthIdentityDisabledError):
        await _exchange(manager)
    current = await users.get_by_id(user.id)
    assert current.is_active is False
