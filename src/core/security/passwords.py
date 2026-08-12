"""Password hashing and password-lifecycle state transitions."""

import asyncio
from typing import TYPE_CHECKING, Iterable, Optional

import bcrypt

from config import config
from utils.time import utc_now

if TYPE_CHECKING:
    from db.models import User


_BCRYPT_MAX_BYTES = 72

DUMMY_PASSWORD_HASH = "$2b$12$mVnKMOjGcfCqIRsSQMoM6uzEEe3tfZKFqAHVbj3w6/P0JBtySBr7W"


def _bcrypt_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_bcrypt_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_bcrypt_bytes(plain), hashed.encode("utf-8"))


async def passwords_match_any(
    plain: str, hashes: Iterable[Optional[str]]
) -> bool:
    targets = [value for value in hashes if value]
    if not targets:
        return False
    results = await asyncio.gather(
        *(asyncio.to_thread(verify_password, plain, value) for value in targets)
    )
    return any(results)


def apply_new_password(
    user: "User", new_hash: str, *, mark_must_change: bool
) -> None:
    old_hash = user.hashed_password
    if old_hash:
        history = list(user.password_history or [])
        history.insert(0, old_hash)
        user.password_history = history[: config.PASSWORD_HISTORY_RETAIN]
        user.password_version = (user.password_version or 0) + 1
    else:
        user.password_history = list(user.password_history or [])

    user.hashed_password = new_hash
    user.password_changed_at = utc_now()
    user.must_change_password = mark_must_change


def password_reuse_candidates(user: "User") -> list[str]:
    count = config.PASSWORD_HISTORY_COUNT
    if count <= 0:
        return []
    candidates: list[str] = []
    if user.hashed_password:
        candidates.append(user.hashed_password)
    candidates.extend(list(user.password_history or [])[: count - 1])
    return candidates
