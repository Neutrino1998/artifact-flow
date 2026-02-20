"""
Shared pytest fixtures for ArtifactFlow.

IMPORTANT: JWT_SECRET must be set BEFORE any `src/api/*` module is imported,
because `api/config.py` has a module-level fail-fast check (line 59).
"""

import os

# --- env must be set before any api import ---
os.environ.setdefault("ARTIFACTFLOW_JWT_SECRET", "test-secret-do-not-use-in-production")

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import create_test_database_manager, DatabaseManager
from db.models import (
    User,
    Conversation,
    Message,
    ArtifactSession,
    Artifact,
    ArtifactVersion,
)
from repositories.user_repo import UserRepository
from repositories.conversation_repo import ConversationRepository
from repositories.artifact_repo import ArtifactRepository
from api.services.auth import hash_password


# ============================================================
# Database fixtures
# ============================================================

# Deletion order respects FK constraints:
#   ArtifactVersion → Artifact → ArtifactSession → Message → Conversation → User
_TABLES_DELETE_ORDER = [
    ArtifactVersion.__tablename__,
    Artifact.__tablename__,
    ArtifactSession.__tablename__,
    Message.__tablename__,
    Conversation.__tablename__,
    User.__tablename__,
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
