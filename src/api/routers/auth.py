"""
Auth Router

处理认证 + 当前用户自助资料管理的 API 端点：
- POST  /api/v1/auth/login        - 登录
- GET   /api/v1/auth/me           - 当前用户信息
- PATCH /api/v1/auth/me           - 自助修改 profile（display_name）
- POST  /api/v1/auth/me/password  - 自助修改密码

用户管理（admin 视角的 CRUD / bulk）放在 routers/admin_users.py，
挂在 /api/v1/admin/users/* 下。
"""

import asyncio
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from config import config
from utils.time import utc_now
from api.dependencies import (
    get_current_user,
    get_department_repository,
    get_login_rate_limiter,
    get_user_repository,
)
from api.schemas.auth import (
    LoginRequest,
    LoginResponse,
    UserInfo,
    ChangePasswordRequest,
    UpdateMyProfileRequest,
)
from api.services.auth import (
    TokenPayload,
    DUMMY_PASSWORD_HASH,
    apply_new_password,
    hash_password,
    passwords_match_any,
    password_reuse_candidates,
    verify_password,
    create_access_token,
)
from repositories.department_repo import DepartmentRepository
from repositories.user_repo import UserRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


def _client_ip(request: Request) -> str:
    """提取客户端 IP 用于 per-IP 频控。

    读 `X-Real-IP`(nginx 用 `proxy_set_header X-Real-IP $remote_addr` **覆写**),
    无则回落 `request.client.host`。

    安全前提:prod / intranet 里 backend 是 `expose`(不 publish 主机端口),只经
    nginx 可达 → nginx 覆写的 `X-Real-IP` **不可被客户端伪造**。SQLite/dev 模式直接
    发布 app 端口、无 nginx,这里会回落 client.host;该模式仅供开发测试,per-IP 不
    做加固(可被伪造但无实际威胁)。

    **刻意不读 `X-Forwarded-For`** —— nginx 用 `$proxy_add_x_forwarded_for` 是
    *追加* 语义,首段是客户端自带的、可伪造(reviewer P1)。per-username 计数才是
    不可伪造的主防线;per-IP 是补抓"同 IP 喷多个用户名"的二级防线。
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def _resolve_dept_path(
    department_id: Optional[str],
    dept_repo: DepartmentRepository,
) -> Optional[list[str]]:
    """
    给 UserInfo.department_path 用 — 把用户的 department_id 翻译成 root → leaf
    的部门名链路。未分配 → None；FK 还没 SET NULL 时遇到孤儿 id → None。
    """
    if not department_id:
        return None
    chain = await dept_repo.get_ancestor_chain(department_id)
    if not chain:
        return None
    return [d.name for d in chain]


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
    rate_limiter=Depends(get_login_rate_limiter),
):
    """用户登录，返回 JWT Token"""
    # 登录入口归一化:去首尾空白。合法用户名不含空格(注册 validate_username
    # 拒空格、CSV 导入 strip),故 strip 只会帮本该成功的登录匹配上,永不会
    # 误匹配到他人。同时让频控 key 归一,避免加空格绕过 per-username 计数。
    username = request.username.strip()
    user_key = f"user:{username}"
    ip_key = f"ip:{_client_ip(http_request)}"

    # 频控预检(ACC-01):任一 key 失败累计超阈 → 锁定窗口内一律 429,连正确
    # 密码也拒(锁定本身就是目的)。per-username 主防线 + per-IP 二级。
    if await rate_limiter.is_locked(user_key) or await rate_limiter.is_locked(ip_key):
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Please try again later.",
            headers={"Retry-After": str(config.LOGIN_FAILURE_WINDOW_SEC)},
        )

    user = await user_repo.get_by_username(username)
    # 时序防枚举(ACC-05):用户不存在时也对固定假 hash 跑一次 bcrypt,让
    # 「用户不存在」与「密码错误」两条分支耗时恒定 —— 否则秒回 401 即暴露
    # 用户名不存在。bcrypt 是 CPU bound (~250ms),丢线程池避免卡 event loop
    # 影响其他用户的 SSE 流。
    hashed = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(verify_password, request.password, hashed)
    if not user or not password_ok:
        # 认证失败 → 两 key 各 +1。账号存在但密码对(下面的 disabled 分支)
        # 不算撞库信号,不计。
        await rate_limiter.record_failure(user_key)
        await rate_limiter.record_failure(ip_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is disabled")

    # 认证成功 → 重置该用户名计数(IP 计数让其自然过期,避免一个合法用户
    # 把同 IP 下他人造成的失败一并清掉)。
    await rate_limiter.reset(user_key)

    # 周期到期(等保):口令龄 > 到期天数 → 置 must_change_password(NULL 视为
    # 已过期)。登录本身不拦,但闸门会把除改密外的请求挡在 403;前端据
    # must_change_password 弹强制改密框。已是 True 则跳过(无谓写库)。
    if (
        config.PASSWORD_EXPIRY_DAYS > 0
        and not user.must_change_password
        and (
            user.password_changed_at is None
            or utc_now() - user.password_changed_at > timedelta(days=config.PASSWORD_EXPIRY_DAYS)
        )
    ):
        user.must_change_password = True
        await user_repo.update(user)

    token = create_access_token(user.id, user.username, user.role, user.password_version)

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=config.JWT_EXPIRY_DAYS * 86400,
        user=UserInfo(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            must_change_password=user.must_change_password,
            department_path=await _resolve_dept_path(user.department_id, dept_repo),
        ),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """获取当前用户信息"""
    user = await user_repo.get_by_id(current_user.user_id)
    return UserInfo(
        id=current_user.user_id,
        username=current_user.username,
        display_name=user.display_name if user else None,
        role=current_user.role,
        must_change_password=user.must_change_password if user else False,
        department_path=await _resolve_dept_path(
            user.department_id if user else None, dept_repo
        ),
    )


@router.post("/me/password", status_code=204)
async def change_my_password(
    request: ChangePasswordRequest,
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """当前用户自助修改密码"""
    user = await user_repo.get_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not await asyncio.to_thread(verify_password, request.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # 不重用查重(等保):新口令不得与「最近 N 个用过的口令(含当前)」相同。
    # N=PASSWORD_HISTORY_COUNT;候选 = [当前 hash] + history[:N-1]。
    if await passwords_match_any(request.new_password, password_reuse_candidates(user)):
        raise HTTPException(
            status_code=400,
            detail="新密码不能与最近使用过的密码相同，请更换",
        )

    new_hash = await asyncio.to_thread(hash_password, request.new_password)
    # 自助改密:不强制再改 → mark_must_change=False(同时清掉可能存在的强制标志)。
    apply_new_password(user, new_hash, mark_must_change=False)
    await user_repo.update(user)

    logger.info(f"Password changed: {user.username} (pwd_v={user.password_version})")


@router.patch("/me", response_model=UserInfo)
async def update_my_profile(
    request: UpdateMyProfileRequest,
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    当前用户自助修改非敏感资料字段（目前仅 display_name）。

    设计意图：与 admin 后台 PUT /admin/users/{id} 解耦 —— role / is_active /
    password 这些安全敏感字段走专门端点，profile 字段走这里。任何已登录
    用户都能调，不需要 admin 权限。
    """
    user = await user_repo.get_by_id(current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if request.display_name is not None:
        # 显式空字符串 = 清空 display_name（落库为 NULL）
        user.display_name = request.display_name.strip() or None

    await user_repo.update(user)

    logger.info(f"Profile updated: {user.username}")

    return UserInfo(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        must_change_password=user.must_change_password,
        department_path=await _resolve_dept_path(user.department_id, dept_repo),
    )
