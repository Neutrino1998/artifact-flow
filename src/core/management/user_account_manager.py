"""Authenticated-user account and login use cases."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from config import config
from core.security import passwords
from core.security.identity import (
    LOCAL_AUTH_PROVIDER,
    can_change_password,
    can_edit_profile,
    local_auth_subject,
)
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


class UserAccountError(Exception):
    def __init__(self, detail: Any):
        self.detail = detail
        super().__init__(str(detail))


class InvalidCredentialsError(UserAccountError):
    pass


class InactiveUserError(UserAccountError):
    pass


class UserAccountNotFoundError(UserAccountError):
    pass


class CurrentPasswordIncorrectError(UserAccountError):
    pass


class PasswordReusedError(UserAccountError):
    pass


class PasswordUnavailableError(UserAccountError):
    pass


class ProfileUnavailableError(UserAccountError):
    pass


class UserAccountManager:
    def __init__(
        self,
        user_repository: UserRepository,
        department_repository: DepartmentRepository,
    ):
        self._users = user_repository
        self._departments = department_repository

    async def _department_path(self, department_id: str | None) -> list[str] | None:
        if not department_id:
            return None
        chain = await self._departments.get_ancestor_chain(department_id)
        return [department.name for department in chain] or None

    async def _profile(self, user) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "must_change_password": user.must_change_password,
            "auth_provider": user.auth_provider,
            "can_change_password": can_change_password(user.auth_provider),
            "can_edit_profile": can_edit_profile(user.auth_provider),
            "department_path": await self._department_path(user.department_id),
        }

    async def authenticate(self, username: str, password: str) -> dict:
        user = await self._users.get_by_auth_identity(
            LOCAL_AUTH_PROVIDER, local_auth_subject(username)
        )
        password_hash = (
            user.hashed_password if user is not None else passwords.DUMMY_PASSWORD_HASH
        )
        password_ok = await asyncio.to_thread(
            passwords.verify_password, password, password_hash
        )
        if user is None or not password_ok:
            raise InvalidCredentialsError("Invalid username or password")
        if not user.is_active:
            raise InactiveUserError("User account is disabled")
        if (
            config.PASSWORD_EXPIRY_DAYS > 0
            and not user.must_change_password
            and (
                user.password_changed_at is None
                or utc_now() - user.password_changed_at
                > timedelta(days=config.PASSWORD_EXPIRY_DAYS)
            )
        ):
            user.must_change_password = True
            await self._users.save_user(user)
        return {
            "profile": await self._profile(user),
            "password_version": user.password_version,
        }

    async def get_profile(self, user_id: str) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserAccountNotFoundError("User not found")
        return await self._profile(user)

    async def change_password(
        self, user_id: str, *, current_password: str, new_password: str
    ) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserAccountNotFoundError("User not found")
        if not can_change_password(user.auth_provider):
            raise PasswordUnavailableError(
                "Password login is unavailable for this account"
            )
        if not await asyncio.to_thread(
            passwords.verify_password, current_password, user.hashed_password or ""
        ):
            raise CurrentPasswordIncorrectError("Current password is incorrect")
        if await passwords.passwords_match_any(
            new_password, passwords.password_reuse_candidates(user)
        ):
            raise PasswordReusedError(
                "新密码不能与最近使用过的密码相同，请更换"
            )
        password_hash = await asyncio.to_thread(passwords.hash_password, new_password)
        passwords.apply_new_password(user, password_hash, mark_must_change=False)
        await self._users.save_user(user)
        logger.info(
            "Password changed: %s (pwd_v=%s)", user.username, user.password_version
        )

    async def update_profile(
        self,
        user_id: str,
        *,
        display_name: str | None,
        display_name_supplied: bool,
    ) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserAccountNotFoundError("User not found")
        if display_name_supplied and not can_edit_profile(user.auth_provider):
            logger.warning(
                "Provider-managed profile update rejected: user_id=%s provider=%s",
                user.id,
                user.auth_provider,
            )
            raise ProfileUnavailableError(
                "Profile is managed by the authentication provider"
            )
        if display_name is not None:
            user.display_name = display_name.strip() or None
            await self._users.save_user(user)
            logger.info("Profile updated: %s", user.username)
        return await self._profile(user)
