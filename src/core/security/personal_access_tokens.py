"""Personal-access-token format, scopes, and one-way secret verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from enum import Enum
from typing import Optional
from uuid import uuid4

from config import config


PAT_BEARER_PREFIX = "af_pat_"
PAT_ID_PREFIX = "pat_"
PAT_MAX_ACTIVE_PER_USER = 50
PAT_MAX_EXPIRY_DAYS = 365
PAT_DEFAULT_EXPIRY_DAYS = 90
PAT_LAST_USED_WRITE_INTERVAL_SECONDS = 60 * 60


class PersonalAccessTokenScope(str, Enum):
    CONVERSATIONS_READ = "conversations:read"
    CONVERSATIONS_WRITE = "conversations:write"
    CONVERSATIONS_CONTROL = "conversations:control"
    CONVERSATIONS_DELETE = "conversations:delete"
    ARTIFACTS_READ = "artifacts:read"
    SKILLS_READ = "skills:read"
    SKILLS_WRITE = "skills:write"
    TOOLS_APPROVE = "tools:approve"


ALL_PAT_SCOPES = frozenset(scope.value for scope in PersonalAccessTokenScope)


def _verification_key() -> bytes:
    """Derive a domain-separated verifier key from the mandatory JWT secret."""
    return hmac.new(
        config.JWT_SECRET.encode("utf-8"),
        b"artifactflow-personal-access-token-v1",
        hashlib.sha256,
    ).digest()


def hash_pat_secret(secret: str) -> str:
    return hmac.new(
        _verification_key(),
        secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def issue_pat() -> tuple[str, str, str]:
    """Return ``(database_id, bearer, secret_hash)`` for one new PAT."""
    public_id = uuid4().hex
    secret = secrets.token_urlsafe(32)
    token_id = f"{PAT_ID_PREFIX}{public_id}"
    bearer = f"{PAT_BEARER_PREFIX}{public_id}_{secret}"
    return token_id, bearer, hash_pat_secret(secret)


def parse_pat(bearer: str) -> Optional[tuple[str, str]]:
    if not bearer.startswith(PAT_BEARER_PREFIX):
        return None
    public_id, separator, secret = bearer[len(PAT_BEARER_PREFIX):].partition("_")
    if (
        not separator
        or len(public_id) != 32
        or any(char not in "0123456789abcdef" for char in public_id)
        or len(secret) < 32
    ):
        return None
    return f"{PAT_ID_PREFIX}{public_id}", secret


def verify_pat_secret(secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_pat_secret(secret), expected_hash)


def display_pat_prefix(token_id: str) -> str:
    public_id = token_id.removeprefix(PAT_ID_PREFIX)
    return f"{PAT_BEARER_PREFIX}{public_id[:8]}…"
