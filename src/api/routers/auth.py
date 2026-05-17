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

from fastapi import APIRouter, Depends, HTTPException

from config import config
from api.dependencies import (
    get_current_user,
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
    hash_password,
    verify_password,
    create_access_token,
)
from repositories.user_repo import UserRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    user_repo: UserRepository = Depends(get_user_repository),
):
    """用户登录，返回 JWT Token"""
    user = await user_repo.get_by_username(request.username)
    # bcrypt 是 CPU bound (~250ms)；丢线程池避免卡 event loop 影响其他用户的 SSE 流
    if not user or not await asyncio.to_thread(verify_password, request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is disabled")

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
        ),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """获取当前用户信息"""
    user = await user_repo.get_by_id(current_user.user_id)
    return UserInfo(
        id=current_user.user_id,
        username=current_user.username,
        display_name=user.display_name if user else None,
        role=current_user.role,
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

    user.hashed_password = await asyncio.to_thread(hash_password, request.new_password)
    user.password_version = (user.password_version or 0) + 1
    await user_repo.update(user)

    logger.info(f"Password changed: {user.username} (pwd_v={user.password_version})")


@router.patch("/me", response_model=UserInfo)
async def update_my_profile(
    request: UpdateMyProfileRequest,
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
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
    )
