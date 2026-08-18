"""Remote bearer exchange and JIT local-principal synchronization."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from core.management.department_manager import DepartmentManager
from core.security.remote_bearer_config import RemoteBearerConfig
from core.security.remote_bearer_userinfo import (
    NormalizedRemoteIdentity,
    RemoteBearerUserInfoClient,
)
from core.security.sso_state import IssuedSsoState, SsoStateStore
from db.models import User
from repositories.base import DuplicateError
from repositories.user_repo import UserRepository, UserWriteError
from utils.logger import get_logger


logger = get_logger("ArtifactFlow")


class RemoteAuthError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class RemoteAuthDisabledError(RemoteAuthError):
    pass


class RemoteAuthStateError(RemoteAuthError):
    pass


class RemoteAuthIdentityDisabledError(RemoteAuthError):
    pass


class RemoteAuthPersistenceError(RemoteAuthError):
    pass


def _with_query_parameter(url: str, name: str, value: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append((name, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


class RemoteAuthManager:
    """Own the complete state→userinfo→department→user exchange sequence."""

    def __init__(
        self,
        provider_config: RemoteBearerConfig,
        state_store: SsoStateStore | None,
        userinfo_client: RemoteBearerUserInfoClient | None,
        user_repository: UserRepository,
        department_manager: DepartmentManager,
    ):
        self._config = provider_config
        self._state_store = state_store
        self._userinfo = userinfo_client
        self._users = user_repository
        self._departments = department_manager

    def callback_uses_https(self) -> bool:
        return bool(
            self._config.login
            and urlsplit(self._config.login.callback_url).scheme == "https"
        )

    def is_enabled(self) -> bool:
        return bool(
            self._config.enabled
            and self._config.provider is not None
            and self._config.login is not None
            and self._config.userinfo is not None
            and self._state_store is not None
            and self._userinfo is not None
        )

    def _require_enabled(self) -> None:
        if not self.is_enabled():
            raise RemoteAuthDisabledError("Enterprise authentication is unavailable")

    async def start(self) -> tuple[str, IssuedSsoState]:
        self._require_enabled()
        assert self._config.login is not None
        assert self._state_store is not None
        issued = await self._state_store.issue(self._config.login.state_ttl_seconds)
        callback = _with_query_parameter(
            self._config.login.callback_url, "af_sso_state", issued.state
        )
        authorization_url = _with_query_parameter(
            self._config.login.url, self._config.login.return_param, callback
        )
        return authorization_url, issued

    async def _sync_user(self, identity: NormalizedRemoteIdentity) -> dict:
        assert self._config.provider is not None
        provider_id = self._config.provider.id
        user = await self._users.get_by_auth_identity(provider_id, identity.subject)
        if user is not None and not user.is_active:
            raise RemoteAuthIdentityDisabledError("User account is disabled")

        department_id = await self._departments.resolve_path(
            list(identity.department_path or ())
        )
        created = False
        if user is None:
            candidate = User(
                id=f"user-{uuid4().hex}",
                auth_provider=provider_id,
                auth_subject=identity.subject,
                username=identity.username,
                hashed_password=None,
                display_name=identity.display_name,
                role="user",
                is_active=True,
                password_version=0,
                must_change_password=False,
                password_changed_at=None,
                password_history=[],
                department_id=department_id,
            )
            try:
                user = await self._users.create_user(candidate)
                created = True
            except DuplicateError:
                user = await self._users.get_by_auth_identity(
                    provider_id, identity.subject
                )
                if user is None:
                    raise RemoteAuthPersistenceError("Remote user creation failed")
            except UserWriteError as exc:
                raise RemoteAuthPersistenceError("Remote user creation failed") from exc

        if not user.is_active:
            raise RemoteAuthIdentityDisabledError("User account is disabled")

        if not created and (
            user.username != identity.username
            or user.display_name != identity.display_name
            or user.department_id != department_id
        ):
            user.username = identity.username
            user.display_name = identity.display_name
            # Explicit upstream no-department state clears a previous assignment.
            user.department_id = department_id
            try:
                await self._users.save_user(user)
            except UserWriteError as exc:
                raise RemoteAuthPersistenceError("Remote user update failed") from exc

        logger.info(
            "Remote identity synchronized: provider=%s user_id=%s created=%s",
            provider_id,
            user.id,
            created,
        )
        return {
            "profile": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.role,
                "must_change_password": False,
                "auth_provider": provider_id,
                "can_change_password": False,
                "department_path": list(identity.department_path)
                if identity.department_path is not None
                else None,
            },
            "password_version": user.password_version,
        }

    async def exchange(
        self, *, state: str, browser_binding: str, upstream_token: str
    ) -> dict:
        self._require_enabled()
        assert self._state_store is not None
        assert self._userinfo is not None
        if len(state) > 256 or len(browser_binding) > 256:
            raise RemoteAuthStateError("Authentication state is invalid or expired")
        if not await self._state_store.consume(state, browser_binding):
            raise RemoteAuthStateError("Authentication state is invalid or expired")
        identity = await self._userinfo.fetch(upstream_token)
        if not identity.enabled:
            raise RemoteAuthIdentityDisabledError("Upstream user account is disabled")
        return await self._sync_user(identity)
