"""
认证服务

提供密码哈希和 JWT Token 签发/验证功能。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import FrozenSet, Literal, Optional

import jwt

from config import config
from core.security.passwords import (
    DUMMY_PASSWORD_HASH,
    apply_new_password,
    hash_password,
    password_reuse_candidates,
    passwords_match_any,
    verify_password,
)


@dataclass
class TokenPayload:
    """JWT Token 解码后的载荷"""
    user_id: str
    username: str
    role: str
    password_version: int = 0


@dataclass(frozen=True)
class AuthPrincipal:
    """Current ordinary-user API actor, authenticated by session JWT or PAT."""

    user_id: str
    username: str
    role: str
    password_version: int
    credential_type: Literal["session", "pat"]
    credential_id: Optional[str]
    scopes: FrozenSet[str]


def create_access_token(
    user_id: str,
    username: str,
    role: str,
    password_version: int = 0,
) -> str:
    """
    签发 JWT Token

    Args:
        user_id: 用户 ID
        username: 用户名
        role: 角色
        password_version: 用户当前的密码版本（改密会递增）

    Returns:
        JWT Token 字符串
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "pwd_v": password_version,
        "iat": now,
        "exp": now + timedelta(seconds=config.JWT_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[TokenPayload]:
    """
    解码并验证 JWT Token

    Args:
        token: JWT Token 字符串

    Returns:
        TokenPayload 或 None（无效/过期）
    """
    try:
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=[config.JWT_ALGORITHM],
        )
        return TokenPayload(
            user_id=payload["sub"],
            username=payload["username"],
            role=payload["role"],
            password_version=payload.get("pwd_v", 0),
        )
    except (jwt.InvalidTokenError, KeyError):
        return None
