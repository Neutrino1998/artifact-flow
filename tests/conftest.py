"""
Shared pytest fixtures for ArtifactFlow.

IMPORTANT: JWT_SECRET must be set BEFORE any module is imported,
because `config.py` has a module-level Settings() instantiation.
"""

import base64
import os

# --- env must be set before any api import ---
os.environ.setdefault("ARTIFACTFLOW_JWT_SECRET", "test-secret-do-not-use-in-production")
# CREDENTIAL_KEY 现为强制启动项(validate_config)——给测试套一把合法 Fernet key,否则
# app lifespan 起不来。需凭证 round-trip 的测试各自 monkeypatch 覆盖成独立 key。
os.environ.setdefault(
    "ARTIFACTFLOW_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"0" * 32).decode()
)
# 测试日志隔离到 tests/logs,别污染生产 data/logs(尤其故意抛异常的中间件/路由
# 测试会写整段 traceback)。必须在任何 app import 前设置,否则 import 时已建好的
# logger 还是指向 data/logs。
os.environ.setdefault("ARTIFACTFLOW_LOG_DIR", "tests/logs")

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import create_test_database_manager, DatabaseManager
from db.models import Base, User
from repositories.user_repo import UserRepository
from repositories.conversation_repo import ConversationRepository
from repositories.artifact_repo import ArtifactRepository
from repositories.department_repo import DepartmentRepository
from api.services.auth import hash_password


# ============================================================
# Database fixtures
# ============================================================

# Deletion order respects FK constraints — derived from Base.metadata so new
# tables are covered automatically (sorted_tables is parent→child; delete in
# reverse). departments is special-cased below (self-FK + ondelete=RESTRICT
# needs iterative leaf-deletion, a blanket DELETE trips the row-level check).
_TABLES_DELETE_ORDER = [
    t.name for t in reversed(Base.metadata.sorted_tables) if t.name != "departments"
]


@pytest.fixture(scope="session")
async def db_manager() -> DatabaseManager:
    """
    Session-scoped in-memory SQLite database.

    Tables are created once; all tests share the same engine.
    """
    manager = create_test_database_manager()
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def db_session(db_manager: DatabaseManager) -> AsyncSession:
    """
    Function-scoped database session with table cleanup on teardown.

    Cannot use savepoint rollback because BaseRepository.add() calls
    commit() internally (src/repositories/base.py:136).
    """
    async with db_manager.session() as session:
        yield session

    # Teardown: delete all rows in FK-safe order
    async with db_manager.session() as cleanup_session:
        for table_name in _TABLES_DELETE_ORDER:
            await cleanup_session.execute(text(f"DELETE FROM {table_name}"))
        # Departments: parent_id self-FK with ondelete=RESTRICT means we can't
        # blanket-delete in one statement (parent rows still referenced when
        # row-level check fires). Iteratively delete leaves until empty.
        while True:
            result = await cleanup_session.execute(text(
                "DELETE FROM departments WHERE id NOT IN "
                "(SELECT parent_id FROM departments WHERE parent_id IS NOT NULL)"
            ))
            if result.rowcount == 0:
                break
        await cleanup_session.commit()


# ============================================================
# Repository fixtures
# ============================================================


@pytest.fixture
def user_repo(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)


@pytest.fixture
def conversation_repo(db_session: AsyncSession) -> ConversationRepository:
    return ConversationRepository(db_session)


@pytest.fixture
def artifact_repo(db_session: AsyncSession) -> ArtifactRepository:
    return ArtifactRepository(db_session)


@pytest.fixture
def department_repo(db_session: AsyncSession) -> DepartmentRepository:
    return DepartmentRepository(db_session)


# ============================================================
# Pre-created user fixtures
# ============================================================


@pytest.fixture
async def test_user(user_repo: UserRepository) -> User:
    """A pre-created regular user."""
    user = User(
        id=str(uuid.uuid4()),
        username="testuser",
        hashed_password=hash_password("testpass"),
        role="user",
        is_active=True,
    )
    return await user_repo.add(user)


@pytest.fixture
async def test_admin(user_repo: UserRepository) -> User:
    """A pre-created admin user."""
    admin = User(
        id=str(uuid.uuid4()),
        username="testadmin",
        hashed_password=hash_password("adminpass"),
        role="admin",
        is_active=True,
    )
    return await user_repo.add(admin)
