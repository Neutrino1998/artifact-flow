"""
Auth Router

处理认证相关的 API 端点：
- POST /api/v1/auth/login - 登录
- GET /api/v1/auth/me - 获取当前用户信息
- POST /api/v1/auth/users - 创建用户（Admin）
- GET /api/v1/auth/users - 用户列表（Admin）
- PUT /api/v1/auth/users/{user_id} - 更新用户（Admin）
"""

from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from config import config
from api.dependencies import (
    get_current_user,
    require_admin,
    get_user_repository,
    get_conversation_manager,
)
from api.schemas.auth import (
    LoginRequest,
    LoginResponse,
    UserInfo,
    CreateUserRequest,
    UpdateUserRequest,
    ChangePasswordRequest,
    UserResponse,
    UserListResponse,
    UserImpactResponse,
)
from api.services.auth import (
    TokenPayload,
    hash_password,
    verify_password,
    create_access_token,
)
from core.conversation_manager import ConversationManager
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

    if not verify_password(request.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.hashed_password = hash_password(request.new_password)
    user.password_version = (user.password_version or 0) + 1
    await user_repo.update(user)

    logger.info(f"Password changed: {user.username} (pwd_v={user.password_version})")


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
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """列出所有用户（仅 Admin）"""
    search_query = q.strip() if q else None
    users = await user_repo.list_users(limit=limit, offset=offset, include_inactive=True, search_query=search_query)
    total = await user_repo.count_users(include_inactive=True, search_query=search_query)

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


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """单查用户（Admin） — 给前端编辑表单初始化用"""
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/users/{user_id}/impact", response_model=UserImpactResponse)
async def get_user_impact(
    user_id: str,
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    硬删用户前的影响数据 — 返回会话数。

    给前端 DangerConfirmModal 显示"将级联删除 N 条会话，操作不可恢复"。
    """
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    count = await conversation_manager.count_user_conversations(user_id)
    return UserImpactResponse(conversation_count=count)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    request: UpdateUserRequest,
    current_user: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """
    更新用户（仅 Admin）

    防误锁：admin 不能改自己的 role 或 is_active。配合 DELETE 路径的
    "不能删自己"保护，足以保证系统始终至少有 1 个活跃 admin
    （操作者必然活跃 → 不能动自己 → 至少剩自己）。
    """
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = user_id == current_user.user_id

    if request.display_name is not None:
        user.display_name = request.display_name or None
    if request.password is not None:
        user.hashed_password = hash_password(request.password)
        # admin 重置密码同样吊销该用户的旧 token
        user.password_version = (user.password_version or 0) + 1
    if request.role is not None:
        if request.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")
        if is_self and request.role != user.role:
            raise HTTPException(status_code=403, detail="Cannot change your own role")
        user.role = request.role
    if request.is_active is not None:
        if is_self and request.is_active != user.is_active:
            raise HTTPException(
                status_code=403,
                detail="Cannot change your own active status",
            )
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


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    current_user: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """
    硬删用户（仅 Admin）

    FK CASCADE 一并删除其所有会话 / messages / events / artifacts。
    若用户当前有正在跑的 engine，被级联删的 conversation 行会被 controller
    post-processing 的 exists() 检查兜住（PR2a），不会撞 FK。

    保护：admin 不能删自己。配合"不能改自己 role/is_active"，足以保证
    系统始终至少 1 个活跃 admin。
    """
    if user_id == current_user.user_id:
        raise HTTPException(status_code=403, detail="Cannot delete yourself")

    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    deleted = await user_repo.hard_delete(user_id)
    if not deleted:
        # 极小概率的 TOCTOU：get_by_id 与 hard_delete 之间用户被删
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"User hard-deleted: {user.username} (id={user_id})")
