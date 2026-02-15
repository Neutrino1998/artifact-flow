"""
Auth Router

处理认证相关的 API 端点：
- POST /api/v1/auth/login - 登录
- GET /api/v1/auth/me - 获取当前用户信息
- POST /api/v1/auth/users - 创建用户（Admin）
- GET /api/v1/auth/users - 用户列表（Admin）
- PUT /api/v1/auth/users/{user_id} - 更新用户（Admin）
"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from api.config import config
from api.dependencies import get_current_user, require_admin, get_user_repository
from api.schemas.auth import (
    LoginRequest,
    LoginResponse,
    UserInfo,
    CreateUserRequest,
    UpdateUserRequest,
    UserResponse,
    UserListResponse,
)
from api.services.auth import (
    TokenPayload,
    hash_password,
    verify_password,
    create_access_token,
)
from repositories.user_repo import UserRepository
from db.models import User
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
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="User account is disabled")

    token = create_access_token(user.id, user.username, user.role)

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
):
    """获取当前用户信息"""
    return UserInfo(
        id=current_user.user_id,
        username=current_user.username,
        display_name=None,
        role=current_user.role,
    )


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest,
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """创建用户（仅 Admin）"""
    # 检查用户名是否已存在
    existing = await user_repo.get_by_username(request.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{request.username}' already exists")

    if request.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

    user = User(
        id=f"user-{uuid4().hex}",
        username=request.username,
        hashed_password=hash_password(request.password),
        display_name=request.display_name,
        role=request.role,
    )
    await user_repo.add(user)

    logger.info(f"User created: {user.username} (role={user.role})")

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/users", response_model=UserListResponse)
async def list_users(
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """列出所有用户（仅 Admin）"""
    users = await user_repo.list_users(include_inactive=True)
    total = await user_repo.count_users(include_inactive=True)

    return UserListResponse(
        users=[
            UserResponse(
                id=u.id,
                username=u.username,
                display_name=u.display_name,
                role=u.role,
                is_active=u.is_active,
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ],
        total=total,
    )


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """更新用户（仅 Admin）"""
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if request.display_name is not None:
        user.display_name = request.display_name
    if request.password is not None:
        user.hashed_password = hash_password(request.password)
    if request.role is not None:
        if request.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
        user.role = request.role
    if request.is_active is not None:
        user.is_active = request.is_active

    await user_repo.update(user)

    logger.info(f"User updated: {user.username}")

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )
