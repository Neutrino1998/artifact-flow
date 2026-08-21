"""Personal access token lifecycle and authentication decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from db.models import PersonalAccessToken
from core.security.personal_access_tokens import (
    ALL_PAT_SCOPES,
    PAT_LAST_USED_WRITE_INTERVAL_SECONDS,
    PAT_MAX_ACTIVE_PER_USER,
    PAT_MAX_EXPIRY_DAYS,
    display_pat_prefix,
    issue_pat,
    parse_pat,
    verify_pat_secret,
)
from repositories.personal_access_token_repo import PersonalAccessTokenRepository
from utils.logger import get_logger
from utils.time import utc_now


logger = get_logger("ArtifactFlow")


class PersonalAccessTokenError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class PersonalAccessTokenLimitExceeded(PersonalAccessTokenError):
    pass


class PersonalAccessTokenNotFound(PersonalAccessTokenError):
    pass


@dataclass(frozen=True)
class AuthenticatedPersonalAccessToken:
    token_id: str
    user_id: str
    scopes: frozenset[str]


class PersonalAccessTokenManager:
    def __init__(self, repository: PersonalAccessTokenRepository):
        self._tokens = repository

    @staticmethod
    def _serialize(token: PersonalAccessToken) -> dict:
        return {
            "id": token.id,
            "name": token.name,
            "prefix": display_pat_prefix(token.id),
            "scopes": list(token.scopes or []),
            "created_at": token.created_at,
            "expires_at": token.expires_at,
            "last_used_at": token.last_used_at,
            "revoked_at": token.revoked_at,
        }

    async def create(
        self,
        *,
        user_id: str,
        name: str,
        scopes: list[str],
        expires_in_days: int,
    ) -> dict:
        normalized_name = name.strip()
        normalized_scopes = list(dict.fromkeys(scopes))
        if not normalized_name:
            raise PersonalAccessTokenError("Token name must not be blank")
        if not normalized_scopes or not set(normalized_scopes).issubset(ALL_PAT_SCOPES):
            raise PersonalAccessTokenError("Invalid personal access token scopes")
        if not 1 <= expires_in_days <= PAT_MAX_EXPIRY_DAYS:
            raise PersonalAccessTokenError(
                f"Token expiry must be between 1 and {PAT_MAX_EXPIRY_DAYS} days"
            )

        now = utc_now()
        await self._tokens.lock_owner(user_id)
        if await self._tokens.count_active(user_id, now) >= PAT_MAX_ACTIVE_PER_USER:
            logger.warning(
                "Personal access token limit reached: user_id=%s limit=%s",
                user_id,
                PAT_MAX_ACTIVE_PER_USER,
            )
            raise PersonalAccessTokenLimitExceeded(
                f"At most {PAT_MAX_ACTIVE_PER_USER} active personal access tokens are allowed"
            )

        token_id, bearer, secret_hash = issue_pat()
        token = await self._tokens.add(PersonalAccessToken(
            id=token_id,
            user_id=user_id,
            name=normalized_name,
            secret_hash=secret_hash,
            scopes=normalized_scopes,
            expires_at=now + timedelta(days=expires_in_days),
        ))
        logger.info(
            "Personal access token created: user_id=%s token_id=%s scopes=%s expires_at=%s",
            user_id,
            token.id,
            normalized_scopes,
            token.expires_at.isoformat(),
        )
        return {**self._serialize(token), "token": bearer}

    async def list(self, user_id: str) -> list[dict]:
        return [
            self._serialize(token)
            for token in await self._tokens.list_active_for_user(user_id, utc_now())
        ]

    async def revoke(self, user_id: str, token_id: str) -> None:
        token = await self._tokens.get_owned(user_id, token_id)
        if token is None:
            raise PersonalAccessTokenNotFound("Personal access token not found")
        if token.revoked_at is None:
            token.revoked_at = utc_now()
            await self._tokens.save(token)
            logger.info(
                "Personal access token revoked: user_id=%s token_id=%s",
                user_id,
                token_id,
            )

    async def authenticate(
        self, bearer: str
    ) -> AuthenticatedPersonalAccessToken | None:
        parsed = parse_pat(bearer)
        if parsed is None:
            return None
        token_id, secret = parsed
        token = await self._tokens.get_by_id(token_id)
        now = utc_now()
        if (
            token is None
            or token.revoked_at is not None
            or token.expires_at <= now
            or not verify_pat_secret(secret, token.secret_hash)
        ):
            return None

        result = AuthenticatedPersonalAccessToken(
            token_id=token.id,
            user_id=token.user_id,
            scopes=frozenset(token.scopes or []),
        )
        if (
            token.last_used_at is None
            or (now - token.last_used_at).total_seconds()
            >= PAT_LAST_USED_WRITE_INTERVAL_SECONDS
        ):
            token.last_used_at = now
            await self._tokens.save(token)
        return result
