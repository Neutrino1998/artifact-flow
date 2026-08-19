"""
UserRepository contract tests.

Covers CRUD, count/pagination, and inherited BaseRepository methods.
"""

import uuid
import traceback

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import User
from repositories.base import DuplicateError
from repositories.user_repo import UserRepository, UserWriteError
from api.services.auth import hash_password


class TestUserCRUD:
    """Basic CRUD operations."""

    async def test_add_and_get_by_id(self, user_repo: UserRepository):
        user = User(
            id=str(uuid.uuid4()),
            auth_provider="local_password",
            auth_subject="alice",
            username="alice",
            hashed_password=hash_password("pw"),
            role="user",
            is_active=True,
        )
        created = await user_repo.add(user)

        fetched = await user_repo.get_by_id(created.id)
        assert fetched is not None
        assert fetched.username == "alice"
        assert fetched.role == "user"
        assert fetched.is_active is True
        assert fetched.created_at is not None

    async def test_get_by_id_nonexistent(self, user_repo: UserRepository):
        result = await user_repo.get_by_id("nonexistent-id")
        assert result is None

    async def test_get_by_auth_identity(self, user_repo: UserRepository, test_user: User):
        fetched = await user_repo.get_by_auth_identity(
            test_user.auth_provider, test_user.auth_subject
        )
        assert fetched is not None
        assert fetched.id == test_user.id

    async def test_get_by_auth_identity_nonexistent(self, user_repo: UserRepository):
        result = await user_repo.get_by_auth_identity(
            "local_password", "no-such-user"
        )
        assert result is None

    async def test_unique_auth_identity_constraint(self, user_repo: UserRepository, test_user: User):
        duplicate = User(
            id=str(uuid.uuid4()),
            auth_provider=test_user.auth_provider,
            auth_subject=test_user.auth_subject,
            username=test_user.username,  # same username
            hashed_password=hash_password("other"),
            role="user",
            is_active=True,
        )
        with pytest.raises(IntegrityError):
            await user_repo.add(duplicate)

    async def test_create_user_normalizes_confirmed_username_race(
        self, user_repo: UserRepository, test_user: User
    ):
        duplicate = User(
            id=str(uuid.uuid4()),
            auth_provider=test_user.auth_provider,
            auth_subject=test_user.auth_subject,
            username=test_user.username,
            hashed_password=hash_password("other"),
            role="user",
            is_active=True,
        )

        with pytest.raises(DuplicateError):
            await user_repo.create_user(duplicate)

    async def test_create_user_preserves_non_username_integrity_error(
        self, user_repo: UserRepository
    ):
        username = f"fk-race-{uuid.uuid4().hex[:8]}"
        user = User(
            id=str(uuid.uuid4()),
            auth_provider="local_password",
            auth_subject=username,
            username=username,
            hashed_password="SENSITIVE_HASH_MUST_NOT_APPEAR",
            role="user",
            is_active=True,
            department_id="deleted-department",
        )

        with pytest.raises(UserWriteError) as exc_info:
            await user_repo.create_user(user)

        cause = exc_info.value.__cause__
        assert str(exc_info.value) == "User write failed"
        assert isinstance(cause, IntegrityError)
        assert "SENSITIVE_HASH_MUST_NOT_APPEAR" not in str(cause)
        assert "SQL parameters hidden" in str(cause)
        rendered_traceback = "".join(
            traceback.format_exception(exc_info.value)
        )
        assert "SENSITIVE_HASH_MUST_NOT_APPEAR" not in rendered_traceback

    async def test_update_user_fields(self, user_repo: UserRepository, test_user: User):
        test_user.display_name = "Updated Name"
        test_user.role = "admin"
        test_user.is_active = False
        await user_repo.update(test_user)

        fetched = await user_repo.get_by_id(test_user.id)
        assert fetched.display_name == "Updated Name"
        assert fetched.role == "admin"
        assert fetched.is_active is False

    async def test_delete_user(self, user_repo: UserRepository, test_user: User):
        deleted = await user_repo.delete_by_id(test_user.id)
        assert deleted is True
        assert await user_repo.get_by_id(test_user.id) is None

    async def test_delete_by_id_nonexistent(self, user_repo: UserRepository):
        result = await user_repo.delete_by_id("nonexistent-id")
        assert result is False


class TestUserCountPagination:
    """Count and pagination queries."""

    async def _create_users(self, user_repo, count, active=True):
        users = []
        for i in range(count):
            username = f"paguser-{uuid.uuid4().hex[:8]}"
            u = User(
                id=str(uuid.uuid4()),
                auth_provider="local_password",
                auth_subject=username,
                username=username,
                hashed_password=hash_password("pw"),
                role="user",
                is_active=active,
            )
            users.append(u)
        return await user_repo.add_all(users)

    async def test_count_users_active_only(self, user_repo: UserRepository):
        await self._create_users(user_repo, 2, active=True)
        await self._create_users(user_repo, 1, active=False)

        count = await user_repo.count_users(include_inactive=False)
        assert count == 2

    async def test_count_users_include_inactive(self, user_repo: UserRepository):
        await self._create_users(user_repo, 2, active=True)
        await self._create_users(user_repo, 1, active=False)

        count = await user_repo.count_users(include_inactive=True)
        assert count == 3

    async def test_list_users_pagination(self, user_repo: UserRepository):
        await self._create_users(user_repo, 5, active=True)

        page = await user_repo.list_users(limit=2, offset=4, include_inactive=True)
        assert len(page) == 1

    async def test_list_users_excludes_inactive(self, user_repo: UserRepository):
        await self._create_users(user_repo, 2, active=True)
        await self._create_users(user_repo, 1, active=False)

        active_users = await user_repo.list_users(include_inactive=False)
        for u in active_users:
            assert u.is_active is True
        assert len(active_users) == 2

    async def test_get_all_with_limit_offset(self, user_repo: UserRepository):
        await self._create_users(user_repo, 5, active=True)

        page = await user_repo.get_all(limit=3, offset=0)
        assert len(page) == 3

        page2 = await user_repo.get_all(limit=3, offset=3)
        assert len(page2) == 2


class TestUserBaseRepo:
    """Inherited BaseRepository methods."""

    async def test_exists_true_and_false(self, user_repo: UserRepository, test_user: User):
        assert await user_repo.exists(test_user.id) is True
        assert await user_repo.exists("nonexistent") is False

    async def test_add_all_batch(self, user_repo: UserRepository):
        users = [
            User(
                id=str(uuid.uuid4()),
                auth_provider="local_password",
                auth_subject=f"batch-{i}",
                username=f"batch-{i}",
                hashed_password=hash_password("pw"),
                role="user",
                is_active=True,
            )
            for i in range(3)
        ]
        result = await user_repo.add_all(users)
        assert len(result) == 3
        for u in result:
            assert await user_repo.exists(u.id) is True

    async def test_count_total(self, user_repo: UserRepository, test_user: User):
        count = await user_repo.count()
        assert count >= 1
