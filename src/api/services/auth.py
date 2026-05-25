"""
认证服务

提供密码哈希和 JWT Token 签发/验证功能。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable, Optional

import bcrypt
import jwt

from config import config
from utils.time import utc_now

if TYPE_CHECKING:
    from db.models import User


@dataclass
class TokenPayload:
    """JWT Token 解码后的载荷"""
    user_id: str
    username: str
    role: str
    password_version: int = 0


# bcrypt 5.0 对 >72 字节明文抛 ValueError(不再像旧版静默截断)。schema 按
# 128 *字符* 限长,多字节口令(如 60 个中文 = 180 字节)能过 schema 却在
# hashpw/checkpw 处炸 → 曾是未捕获 500(ACC-04)。统一在 hash 与 verify 两处
# 截到 72 字节(必须一致,否则同一明文哈希/校验对不上)。bcrypt 本就只取前
# 72 字节,有效熵封顶在此是其固有特性;72 边界切断多字节字符对按字节哈希的
# bcrypt 无影响。全局 ValueError→400 handler(main.py)是第二层兜底。
_BCRYPT_MAX_BYTES = 72

# 登录时序防枚举(ACC-05):用户不存在时也对这个固定假 hash 跑一次 checkpw,
# 让「用户不存在」与「密码错误」两条分支耗时恒定。这是一个真实 bcrypt hash,
# 任何明文都不会匹配;静态写死避免 import 期 CPU。
DUMMY_PASSWORD_HASH = "$2b$12$mVnKMOjGcfCqIRsSQMoM6uzEEe3tfZKFqAHVbj3w6/P0JBtySBr7W"


def _bcrypt_bytes(plain: str) -> bytes:
    """明文 → bcrypt 入参字节,截到 72 字节上限。hash/verify 共用以保证一致。"""
    return plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """哈希密码"""
    return bcrypt.hashpw(_bcrypt_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(_bcrypt_bytes(plain), hashed.encode("utf-8"))


async def passwords_match_any(plain: str, hashes: Iterable[Optional[str]]) -> bool:
    """明文是否命中给定 hash 集合中任意一个（改密「不重用」查重）。

    每个 checkpw 都是 CPU bound,丢线程池并行;空集合直接 False。
    """
    targets = [h for h in hashes if h]
    if not targets:
        return False
    results = await asyncio.gather(
        *(asyncio.to_thread(verify_password, plain, h) for h in targets)
    )
    return any(results)


def apply_new_password(user: "User", new_hash: str, *, mark_must_change: bool) -> None:
    """把新口令 hash 落到 user 上,并统一维护密码策略相关字段。

    调用方负责:① 事先算好 new_hash(to_thread hash_password);② 事后
    user_repo.update(user) / add(user) 落库。本函数只做内存态字段变更。

    语义:
    - 若 user 已有旧 hash(改密 / admin 重置)→ 把旧 hash 压入 password_history
      头部、trim 到 PASSWORD_HISTORY_RETAIN,并 pwd_v += 1(吊销旧 token)。
    - 若无旧 hash(新建用户)→ history 保持、pwd_v 不动(无旧 token 可吊销)。
    - password_changed_at 记 utc_now();must_change_password 置 mark_must_change。
    """
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
    """改密查重应比对的 hash 列表 = [当前 hash] + history[:COUNT-1]。

    PASSWORD_HISTORY_COUNT 语义:「新口令不得与最近 N 个用过的口令(含当前)相同」。
    N=1 → 仅当前;N=3 → 当前 + 最近 2 个历史。列里存了 RETAIN 个,这里按 COUNT 取。
    """
    count = config.PASSWORD_HISTORY_COUNT
    if count <= 0:
        return []
    candidates: list[str] = []
    if user.hashed_password:
        candidates.append(user.hashed_password)
    candidates.extend(list(user.password_history or [])[: count - 1])
    return candidates


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
            password_version=payload.get("pwd_v", 0),
        )
    except (jwt.InvalidTokenError, KeyError):
        return None
