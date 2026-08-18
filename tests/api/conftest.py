"""
API test fixtures.

Provides httpx AsyncClient instances that talk to the FastAPI app
in-process via ASGITransport (no server needed).

Dependency overrides:
- get_db_manager    → test db_manager (session-scoped in-memory SQLite)
- get_stream_transport → fresh InMemoryStreamTransport per test
- runtime services → fresh Store/Supervisor/Conversation service per test

NOT overridden (by design):
- get_db_session: uses the real implementation, which creates a fresh
  AsyncSession per request from the overridden db_manager. This preserves
  the "one session per request" production semantics.  The module-level
  _db_manager global is set directly so that get_db_session's internal
  call to get_db_manager() resolves correctly without Depends().
- get_current_user: real JWT verification is used. Fixtures sign tokens
  with the same JWT_SECRET set in tests/conftest.py.
- Execution engine endpoints (POST /chat, resume) require ConversationExecutionService
  and StreamTransport. Override will be added when chat/stream integration
  tests are implemented.
"""

import pytest
from httpx import ASGITransport, AsyncClient

import api.dependencies as deps
from api.main import create_app
from api.dependencies import (
    get_db_manager,
    get_stream_transport,
    get_conversation_execution_service,
    get_runtime_status_reader,
    get_runtime_store,
    get_task_supervisor,
    get_login_rate_limiter,
    get_sso_start_rate_limiter,
)
from api.services.auth import create_access_token
from api.services.stream_transport import InMemoryStreamTransport
from api.services.conversation_execution_service import ConversationExecutionService
from api.services.conversation_lease import ConversationLeaseCoordinator
from api.services.runtime_status_reader import RuntimeStatusReader
from api.services.runtime_store import InMemoryRuntimeStore
from core.execution.task_supervisor import TaskSupervisor
from core.security.remote_bearer_config import RemoteBearerConfig
from api.services.login_rate_limiter import InMemoryLoginRateLimiter
from api.services.sso_rate_limiter import InMemorySsoStartRateLimiter
from config import config
from db.database import DatabaseManager
from db.models import User


@pytest.fixture
async def app(db_manager: DatabaseManager):
    """
    FastAPI app with dependency overrides pointing to test instances.

    ASGITransport does not trigger ASGI lifespan, so init_globals()
    never runs and production singletons stay None.  We set the
    module-level _db_manager so that get_db_session() (which calls
    get_db_manager() directly, not via Depends) works correctly.
    """
    application = create_app()

    stream_transport = InMemoryStreamTransport(ttl_seconds=30)
    runtime_store = InMemoryRuntimeStore()
    task_supervisor = TaskSupervisor(max_concurrent=5)
    execution_service = ConversationExecutionService(
        db_manager=db_manager,
        store=runtime_store,
        stream_transport=stream_transport,
        lease_coordinator=ConversationLeaseCoordinator(runtime_store, lease_ttl=0),
        task_supervisor=task_supervisor,
    )
    runtime_status = RuntimeStatusReader(
        runtime_store, runtime_store, stream_transport
    )
    # 每个 test 一个全新 InMemory 频控器 —— 失败计数不跨 test 泄漏(尤其
    # per-IP key:ASGITransport 下所有请求共享同一 client IP)。
    login_rate_limiter = InMemoryLoginRateLimiter(
        max_failures=config.LOGIN_MAX_FAILURES,
        window_sec=config.LOGIN_FAILURE_WINDOW_SEC,
    )
    sso_start_rate_limiter = InMemorySsoStartRateLimiter(
        per_ip_limit=config.SSO_START_IP_MAX_REQUESTS,
        global_limit=config.SSO_START_GLOBAL_MAX_REQUESTS,
        window_seconds=config.SSO_START_RATE_WINDOW_SEC,
    )

    # Set module-level global so get_db_session()'s direct call works
    old_db_manager = deps._db_manager
    old_remote_config = deps._remote_bearer_config
    old_sso_state_store = deps._sso_state_store
    old_sso_start_rate_limiter = deps._sso_start_rate_limiter
    old_remote_userinfo_client = deps._remote_userinfo_client
    deps._db_manager = db_manager
    deps._remote_bearer_config = RemoteBearerConfig()
    deps._sso_state_store = None
    deps._sso_start_rate_limiter = sso_start_rate_limiter
    deps._remote_userinfo_client = None

    application.dependency_overrides[get_db_manager] = lambda: db_manager
    application.dependency_overrides[get_stream_transport] = lambda: stream_transport
    application.dependency_overrides[get_conversation_execution_service] = lambda: execution_service
    application.dependency_overrides[get_runtime_status_reader] = lambda: runtime_status
    application.dependency_overrides[get_runtime_store] = lambda: runtime_store
    application.dependency_overrides[get_task_supervisor] = lambda: task_supervisor
    application.dependency_overrides[get_login_rate_limiter] = lambda: login_rate_limiter
    application.dependency_overrides[get_sso_start_rate_limiter] = (
        lambda: sso_start_rate_limiter
    )

    yield application

    application.dependency_overrides.clear()
    deps._db_manager = old_db_manager
    deps._remote_bearer_config = old_remote_config
    deps._sso_state_store = old_sso_state_store
    deps._sso_start_rate_limiter = old_sso_start_rate_limiter
    deps._remote_userinfo_client = old_remote_userinfo_client
    await execution_service.shutdown()


@pytest.fixture
async def client(app, test_user: User):
    """Authenticated client for a regular user."""
    token = create_access_token(
        user_id=test_user.id,
        username=test_user.username,
        role=test_user.role,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


@pytest.fixture
async def admin_client(app, test_admin: User):
    """Authenticated client for an admin user."""
    token = create_access_token(
        user_id=test_admin.id,
        username=test_admin.username,
        role=test_admin.role,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as c:
        yield c


@pytest.fixture
async def anon_client(app):
    """Unauthenticated client for testing 401 responses."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as c:
        yield c
