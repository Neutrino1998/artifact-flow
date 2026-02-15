"""
认证服务

提供密码哈希和 JWT Token 签发/验证功能。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from api.config import config


@dataclass
class TokenPayload:
    """JWT Token 解码后的载荷"""
    user_id: str
    username: str
    role: str


def hash_password(plain: str) -> str:
    """哈希密码"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str, username: str, role: str) -> str:
    """
    签发 JWT Token

    Args:
        user_id: 用户 ID
        username: 用户名
        role: 角色

    Returns:
        JWT Token 字符串
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=config.JWT_EXPIRY_DAYS),
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
        )
    except (jwt.InvalidTokenError, KeyError):
        return None
