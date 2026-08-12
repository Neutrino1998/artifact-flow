"""
Shared pytest fixtures for ArtifactFlow.

IMPORTANT: JWT_SECRET must be set BEFORE any module is imported,
because `config.py` has a module-level Settings() instantiation.
"""

import base64
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

# --- env must be set before any api import ---
os.environ.setdefault("ARTIFACTFLOW_JWT_SECRET", "test-secret-do-not-use-in-production")
# CREDENTIAL_KEY 现为强制启动项(validate_config)——给测试套一把合法 Fernet key,否则
# app lifespan 起不来。需凭证 round-trip 的测试各自 monkeypatch 覆盖成独立 key。
os.environ.setdefault(
    "ARTIFACTFLOW_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"0" * 32).decode()
)
# 每个 pytest 进程使用独立临时日志目录，避免 xdist worker 共享
# RotatingFileHandler 的 rename/覆盖竞态。必须在任何项目模块 import 前设置；
# pytest_unconfigure 会在正常退出时关闭该目录下的 handler 并精确删除目录。
_test_run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID", f"local-{os.getpid()}")
_test_worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
_test_log_label = re.sub(
    r"[^A-Za-z0-9._-]",
    "-",
    f"{_test_run_id}-{_test_worker_id}",
)
_TEST_LOG_DIR = Path(tempfile.mkdtemp(prefix=f"artifactflow-pytest-{_test_log_label}-"))
os.environ["ARTIFACTFLOW_LOG_DIR"] = str(_TEST_LOG_DIR)

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


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config) -> None:
    """Close this process's file handlers, then remove its exact temp log dir."""
    log_root = _TEST_LOG_DIR.resolve()
    for logger_object in list(logging.Logger.manager.loggerDict.values()):
        if not isinstance(logger_object, logging.Logger):
            continue
        for handler in list(logger_object.handlers):
            filename = getattr(handler, "baseFilename", None)
            if filename is None:
                continue
            if not Path(filename).resolve().is_relative_to(log_root):
                continue
            logger_object.removeHandler(handler)
            handler.close()
    shutil.rmtree(_TEST_LOG_DIR)


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


@pytest.fixture(scope="session")
def standard_password_hashes() -> dict[str, str]:
    """Hash shared fixture passwords once per pytest worker.

    bcrypt hashes are immutable and carry their work factor in the encoded value.
    Recomputing the same two production-cost hashes for every DB-backed test adds
    CPU time without improving isolation; authentication-specific tests still call
    ``hash_password`` directly when hashing behavior is the subject under test.
    """
    return {
        "testpass": hash_password("testpass"),
        "adminpass": hash_password("adminpass"),
    }


@pytest.fixture
async def test_user(
    user_repo: UserRepository,
    standard_password_hashes: dict[str, str],
) -> User:
    """A pre-created regular user."""
    user = User(
        id=str(uuid.uuid4()),
        username="testuser",
        hashed_password=standard_password_hashes["testpass"],
        role="user",
        is_active=True,
    )
    return await user_repo.add(user)


@pytest.fixture
async def test_admin(
    user_repo: UserRepository,
    standard_password_hashes: dict[str, str],
) -> User:
    """A pre-created admin user."""
    admin = User(
        id=str(uuid.uuid4()),
        username="testadmin",
        hashed_password=standard_password_hashes["adminpass"],
        role="admin",
        is_active=True,
    )
    return await user_repo.add(admin)
