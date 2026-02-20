"""
API test fixtures.

Provides httpx AsyncClient instances that talk to the FastAPI app
in-process via ASGITransport (no server needed).

Dependency overrides:
- get_db_manager    → test db_manager (session-scoped in-memory SQLite)
- get_stream_manager → fresh StreamManager per test
- get_task_manager   → fresh TaskManager per test

NOT overridden (by design):
- get_db_session: uses the real implementation, which creates a fresh
  AsyncSession per request from the overridden db_manager. This preserves
  the "one session per request" production semantics.  The module-level
  _db_manager global is set directly so that get_db_session's internal
  call to get_db_manager() resolves correctly without Depends().
- get_current_user: real JWT verification is used. Fixtures sign tokens
  with the same JWT_SECRET set in tests/conftest.py.
- get_checkpointer: LangGraph execution endpoints (POST /chat, resume)
  are NOT testable with this fixture set. Override will be added when
  chat/stream integration tests are implemented.
"""

import pytest
from httpx import ASGITransport, AsyncClient

import api.dependencies as deps
from api.main import create_app
from api.dependencies import (
    get_db_manager,
    get_stream_manager,
    get_task_manager,
)
from api.services.auth import create_access_token
from api.services.stream_manager import StreamManager
from api.services.task_manager import TaskManager
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

    stream_manager = StreamManager(ttl_seconds=30)
    task_manager = TaskManager(max_concurrent=5)

    # Set module-level global so get_db_session()'s direct call works
    old_db_manager = deps._db_manager
    deps._db_manager = db_manager

    application.dependency_overrides[get_db_manager] = lambda: db_manager
    application.dependency_overrides[get_stream_manager] = lambda: stream_manager
    application.dependency_overrides[get_task_manager] = lambda: task_manager

    yield application

    application.dependency_overrides.clear()
    deps._db_manager = old_db_manager
    await task_manager.shutdown()


@pytest.fixture
async def client(app, test_user: User) -> AsyncClient:
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
async def admin_client(app, test_admin: User) -> AsyncClient:
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
async def anon_client(app) -> AsyncClient:
    """Unauthenticated client for testing 401 responses."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as c:
        yield c
