"""Administrative user-management use cases."""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from uuid import uuid4

from config import config
from core.security.identity import (
    LOCAL_AUTH_PROVIDER,
    can_change_password,
    can_edit_profile,
    local_auth_subject,
)
from core.management.conversation_manager import ConversationManager
from core.management.department_manager import DepartmentManager
from core.security.passwords import apply_new_password, hash_password
from db.models import User
from repositories.base import DuplicateError
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository, UserWriteError
from utils.csv_import import (
    DEPT_NAME_MAX,
    DISPLAY_NAME_MAX,
    PASSWORD_MAX,
    CsvParseError,
    ParsedRow,
    parse_user_csv,
)
from utils.logger import get_logger
from utils.password_policy import validate_password_strength
from utils.validators import validate_username

logger = get_logger("ArtifactFlow")


class AdminUserError(Exception):
    def __init__(self, detail: Any):
        self.detail = detail
        super().__init__(str(detail))


class AdminUserNotFoundError(AdminUserError):
    pass


class AdminUserConflictError(AdminUserError):
    pass


class AdminUserInvalidError(AdminUserError):
    pass


class AdminUserForbiddenError(AdminUserError):
    pass


class AdminUserPayloadTooLargeError(AdminUserError):
    pass


class AdminUserManager:
    def __init__(
        self,
        user_repository: UserRepository,
        department_repository: DepartmentRepository,
        department_manager: DepartmentManager,
        conversation_manager: ConversationManager,
    ):
        self._users = user_repository
        self._departments = department_repository
        self._department_manager = department_manager
        self._conversations = conversation_manager

    @staticmethod
    def _serialize(user: User) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "is_active": user.is_active,
            "auth_provider": user.auth_provider,
            "can_change_password": can_change_password(user.auth_provider),
            "can_edit_profile": can_edit_profile(user.auth_provider),
            "department_id": user.department_id,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }

    async def _require_department(self, department_id: Optional[str]) -> None:
        if (
            department_id is not None
            and await self._departments.get_by_id(department_id) is None
        ):
            raise AdminUserInvalidError(
                "department_id does not reference an existing department"
            )

    async def create(
        self,
        *,
        username: str,
        password: str,
        display_name: Optional[str],
        role: str,
        department_id: Optional[str],
    ) -> dict:
        if await self._users.get_by_auth_identity(
            LOCAL_AUTH_PROVIDER, local_auth_subject(username)
        ) is not None:
            raise AdminUserConflictError(f"Username '{username}' already exists")
        if role not in ("admin", "user"):
            raise AdminUserInvalidError("Role must be 'admin' or 'user'")
        await self._require_department(department_id)
        password_hash = await asyncio.to_thread(hash_password, password)
        user = User(
            id=f"user-{uuid4().hex}",
            auth_provider=LOCAL_AUTH_PROVIDER,
            auth_subject=local_auth_subject(username),
            username=username,
            display_name=display_name,
            role=role,
            department_id=department_id,
        )
        apply_new_password(user, password_hash, mark_must_change=True)
        try:
            await self._users.create_user(user)
        except DuplicateError as exc:
            raise AdminUserConflictError(
                f"Username '{username}' already exists"
            ) from exc
        except UserWriteError as exc:
            if (
                department_id is not None
                and await self._departments.get_by_id(department_id) is None
            ):
                logger.warning(
                    "User creation rejected after department %s disappeared",
                    department_id,
                )
                raise AdminUserInvalidError(
                    "department_id does not reference an existing department"
                ) from exc
            raise
        logger.info("User created: %s (role=%s)", user.username, user.role)
        return self._serialize(user)

    async def list(
        self, *, limit: int, offset: int, query: Optional[str]
    ) -> tuple[list[dict], int]:
        search_query = query.strip() if query else None
        users = await self._users.list_users(
            limit=limit,
            offset=offset,
            include_inactive=True,
            search_query=search_query,
        )
        total = await self._users.count_users(
            include_inactive=True, search_query=search_query
        )
        return [self._serialize(user) for user in users], total

    async def get(self, user_id: str) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AdminUserNotFoundError("User not found")
        return self._serialize(user)

    async def impact(self, user_id: str) -> int:
        if await self._users.get_by_id(user_id) is None:
            raise AdminUserNotFoundError("User not found")
        return await self._conversations.count_user_conversations(user_id)

    async def bulk_impact(self, user_ids: list[str]) -> tuple[int, int]:
        unique_ids = list({user_id for user_id in user_ids if user_id})
        count = await self._conversations.count_users_conversations(unique_ids)
        return len(unique_ids), count

    async def bulk_action(
        self,
        *,
        user_ids: list[str],
        action: str,
        payload: Optional[dict],
        actor_user_id: str,
    ) -> dict:
        target_department_id: Optional[str] = None
        if action == "set_department":
            effective_payload = payload or {}
            if "department_id" not in effective_payload:
                raise AdminUserInvalidError(
                    "set_department requires payload.department_id (use null to clear)"
                )
            target_department_id = effective_payload["department_id"]
            if target_department_id is not None and not isinstance(
                target_department_id, str
            ):
                raise AdminUserInvalidError(
                    "payload.department_id must be a string or null"
                )
            await self._require_department(target_department_id)

        succeeded: list[str] = []
        failed: list[dict] = []
        provider_managed_rejection_count = 0
        provider_managed_rejection_providers: set[str] = set()
        seen: set[str] = set()
        for user_id in user_ids:
            if user_id in seen:
                continue
            seen.add(user_id)
            if user_id == actor_user_id:
                failed.append({"id": user_id, "reason": "forbidden_self"})
                continue
            if action == "delete":
                if await self._users.hard_delete(user_id):
                    succeeded.append(user_id)
                else:
                    failed.append({"id": user_id, "reason": "not_found"})
                continue

            user = await self._users.get_by_id(user_id)
            if user is None:
                failed.append({"id": user_id, "reason": "not_found"})
                continue
            if action == "disable":
                user.is_active = False
            elif action == "enable":
                user.is_active = True
            elif action == "set_department":
                if not can_edit_profile(user.auth_provider):
                    provider_managed_rejection_count += 1
                    provider_managed_rejection_providers.add(user.auth_provider)
                    failed.append(
                        {
                            "id": user_id,
                            "reason": "profile_managed_by_provider",
                        }
                    )
                    continue
                user.department_id = target_department_id
            else:
                failed.append({"id": user_id, "reason": "internal_error"})
                continue
            try:
                await self._users.save_user(user)
            except UserWriteError:
                logger.warning(
                    "bulk_user_action %s failed for %s due to a user write "
                    "integrity error",
                    action,
                    user_id,
                )
                failed.append({"id": user_id, "reason": "internal_error"})
                continue
            succeeded.append(user_id)

        if provider_managed_rejection_count:
            logger.warning(
                "Bulk department update skipped provider-managed users: "
                "actor_user_id=%s rejected_count=%d providers=%s",
                actor_user_id,
                provider_managed_rejection_count,
                ",".join(sorted(provider_managed_rejection_providers)),
            )
        logger.info(
            "Bulk action %r done: succeeded=%d failed=%d",
            action,
            len(succeeded),
            len(failed),
        )
        return {"succeeded": succeeded, "failed": failed}

    @staticmethod
    def _validate_department_path(row: ParsedRow) -> Optional[list[str]]:
        levels = [row.dept_l1, row.dept_l2, row.dept_l3]
        last_non_empty = max(
            (index for index, value in enumerate(levels) if value), default=-1
        )
        if last_non_empty < 0:
            return None
        for index in range(last_non_empty + 1):
            if not levels[index]:
                raise ValueError(
                    "department levels must be contiguous "
                    f"(dept_l{index + 1} empty but a deeper level is set)"
                )
        return levels[: last_non_empty + 1]

    @staticmethod
    def _validate_field_lengths(row: ParsedRow) -> None:
        if len(row.display_name) > DISPLAY_NAME_MAX:
            raise ValueError(
                f"display_name too long: {len(row.display_name)} chars "
                f"(max {DISPLAY_NAME_MAX})"
            )
        if row.password and len(row.password) > PASSWORD_MAX:
            raise ValueError(
                f"password too long: {len(row.password)} chars (max {PASSWORD_MAX})"
            )
        for index, name in enumerate(
            (row.dept_l1, row.dept_l2, row.dept_l3), start=1
        ):
            if name and len(name) > DEPT_NAME_MAX:
                raise ValueError(
                    f"dept_l{index} too long: {len(name)} chars (max {DEPT_NAME_MAX})"
                )

    async def bulk_import(self, raw: bytes) -> dict:
        if len(raw) > config.MAX_BULK_IMPORT_BYTES:
            raise AdminUserPayloadTooLargeError(
                f"File too large: {len(raw) / 1024 / 1024:.1f}MB "
                f"(max {config.MAX_BULK_IMPORT_BYTES / 1024 / 1024:.0f}MB)"
            )
        try:
            parsed = parse_user_csv(raw, max_rows=config.MAX_BULK_IMPORT_ROWS)
        except CsvParseError as exc:
            raise AdminUserInvalidError(str(exc)) from exc
        if parsed.duplicate_rows:
            raise AdminUserInvalidError(
                {
                    "message": "CSV contains duplicate usernames within the file",
                    "duplicate_rows": [
                        {"row": row, "username": username}
                        for row, username in parsed.duplicate_rows
                    ],
                }
            )

        existing = await self._users.find_existing_local_usernames(
            {row.username for row in parsed.rows if row.username}
        )
        created: list[dict] = []
        failed: list[dict] = []
        skipped: list[dict] = []
        department_cache: dict[tuple[str, ...], Optional[str]] = {}
        pending: list[tuple[ParsedRow, str, Optional[str]]] = []

        for row in parsed.rows:
            if not row.username:
                failed.append(
                    {"row": row.row_number, "username": None, "reason": "username is required"}
                )
                continue
            try:
                validate_username(row.username)
            except ValueError as exc:
                failed.append(
                    {"row": row.row_number, "username": row.username, "reason": str(exc)}
                )
                continue
            if row.username in existing:
                skipped.append(
                    {
                        "row": row.row_number,
                        "username": row.username,
                        "reason": "username_exists",
                    }
                )
                continue
            try:
                self._validate_field_lengths(row)
                if not row.password:
                    raise ValueError("password is required (column must not be empty)")
                try:
                    validate_password_strength(row.password)
                except ValueError as exc:
                    raise ValueError(f"password does not meet policy: {exc}") from exc
                path = self._validate_department_path(row)
            except ValueError as exc:
                failed.append(
                    {"row": row.row_number, "username": row.username, "reason": str(exc)}
                )
                continue

            department_id: Optional[str] = None
            if path is not None:
                cache_key = tuple(segment.strip() for segment in path)
                if cache_key not in department_cache:
                    department_cache[cache_key] = (
                        await self._department_manager.resolve_path(path)
                    )
                department_id = department_cache[cache_key]
            pending.append((row, row.password, department_id))
            existing.add(row.username)

        try:
            password_hashes = (
                await asyncio.gather(
                    *(
                        asyncio.to_thread(hash_password, password)
                        for _, password, _ in pending
                    )
                )
                if pending
                else []
            )
        except Exception:
            logger.exception("Bulk password hashing failed (%d users)", len(pending))
            raise

        for (row, _password, department_id), password_hash in zip(
            pending, password_hashes
        ):
            user = User(
                id=f"user-{uuid4().hex}",
                auth_provider=LOCAL_AUTH_PROVIDER,
                auth_subject=local_auth_subject(row.username),
                username=row.username,
                display_name=row.display_name or None,
                role="user",
                department_id=department_id,
            )
            apply_new_password(user, password_hash, mark_must_change=True)
            try:
                await self._users.create_user(user)
            except DuplicateError:
                skipped.append(
                    {
                        "row": row.row_number,
                        "username": row.username,
                        "reason": "username_exists",
                    }
                )
                continue
            except UserWriteError as exc:
                if (
                    department_id is not None
                    and await self._departments.get_by_id(department_id) is None
                ):
                    logger.warning(
                        "Bulk user import row %d rejected after department %s "
                        "disappeared",
                        row.row_number,
                        department_id,
                    )
                    failed.append(
                        {
                            "row": row.row_number,
                            "username": row.username,
                            "reason": (
                                "department_id does not reference an existing "
                                "department"
                            ),
                        }
                    )
                    continue
                raise
            created.append(self._serialize(user))

        logger.info(
            "Bulk import done: total=%d created=%d failed=%d skipped=%d",
            len(parsed.rows),
            len(created),
            len(failed),
            len(skipped),
        )
        return {
            "created": created,
            "failed": failed,
            "skipped": skipped,
            "total_rows": len(parsed.rows),
            "detected_encoding": parsed.detected_encoding,
            "warnings": list(parsed.warnings),
        }

    async def update(
        self,
        user_id: str,
        *,
        actor_user_id: str,
        display_name: Optional[str],
        display_name_supplied: bool,
        password: Optional[str],
        role: Optional[str],
        is_active: Optional[bool],
        department_id: Optional[str],
        department_supplied: bool,
    ) -> dict:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AdminUserNotFoundError("User not found")
        is_self = user_id == actor_user_id
        if not can_edit_profile(user.auth_provider) and (
            display_name_supplied or department_supplied
        ):
            logger.warning(
                "Admin provider-managed profile update rejected: "
                "actor_user_id=%s target_user_id=%s provider=%s",
                actor_user_id,
                user.id,
                user.auth_provider,
            )
            raise AdminUserInvalidError(
                "Profile is managed by the authentication provider"
            )
        if password is not None and not can_change_password(user.auth_provider):
            raise AdminUserInvalidError(
                "Password login is unavailable for this account"
            )
        if display_name_supplied:
            user.display_name = display_name or None
        if password is not None:
            if is_self:
                raise AdminUserForbiddenError(
                    "Use POST /auth/me/password to change your own password"
                )
            password_hash = await asyncio.to_thread(hash_password, password)
            apply_new_password(user, password_hash, mark_must_change=True)
        if role is not None:
            if role not in ("admin", "user"):
                raise AdminUserInvalidError("Role must be 'admin' or 'user'")
            if is_self and role != user.role:
                raise AdminUserForbiddenError("Cannot change your own role")
            user.role = role
        if is_active is not None:
            if is_self and is_active != user.is_active:
                raise AdminUserForbiddenError(
                    "Cannot change your own active status"
                )
            user.is_active = is_active
        if department_supplied:
            await self._require_department(department_id)
            user.department_id = department_id
        try:
            await self._users.save_user(user)
        except UserWriteError as exc:
            raise AdminUserInvalidError("Unable to update user") from exc
        logger.info("User updated: %s", user.username)
        return self._serialize(user)

    async def delete(self, user_id: str, *, actor_user_id: str) -> None:
        if user_id == actor_user_id:
            raise AdminUserForbiddenError("Cannot delete yourself")
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise AdminUserNotFoundError("User not found")
        if not await self._users.hard_delete(user_id):
            raise AdminUserNotFoundError("User not found")
        logger.info("User hard-deleted: %s (id=%s)", user.username, user_id)
