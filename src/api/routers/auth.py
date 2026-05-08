"""
Auth Router

处理认证相关的 API 端点：
- POST /api/v1/auth/login - 登录
- GET /api/v1/auth/me - 获取当前用户信息
- POST /api/v1/auth/users - 创建用户（Admin）
- GET /api/v1/auth/users - 用户列表（Admin）
- PUT /api/v1/auth/users/{user_id} - 更新用户（Admin）
"""

import asyncio
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.exc import IntegrityError

from config import config
from api.dependencies import (
    get_current_user,
    require_admin,
    get_user_repository,
    get_conversation_manager,
    get_department_repository,
)
from api.schemas.auth import (
    MAX_BULK_USER_ACTION_IDS,
    LoginRequest,
    LoginResponse,
    UserInfo,
    CreateUserRequest,
    UpdateUserRequest,
    ChangePasswordRequest,
    UpdateMyProfileRequest,
    UserResponse,
    UserListResponse,
    UserImpactResponse,
    BulkImportFailedRow,
    BulkImportSkippedRow,
    BulkImportResponse,
    BulkActionRequest,
    BulkActionResponse,
    BulkActionFailedItem,
    BulkImpactResponse,
)
from api.services.auth import (
    TokenPayload,
    hash_password,
    verify_password,
    create_access_token,
)
from core.conversation_manager import ConversationManager
from repositories.user_repo import UserRepository
from repositories.department_repo import DepartmentRepository
from db.models import User
from utils.csv_import import (
    DEPT_NAME_MAX,
    DISPLAY_NAME_MAX,
    PASSWORD_MAX,
    CsvParseError,
    ParsedRow,
    parse_user_csv,
)
from utils.department_resolve import resolve_department_path
from utils.logger import get_logger
from utils.validators import validate_username

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


@router.patch("/me", response_model=UserInfo)
async def update_my_profile(
    request: UpdateMyProfileRequest,
    current_user: TokenPayload = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
):
    """
    当前用户自助修改非敏感资料字段（目前仅 display_name）。

    设计意图：与 admin 后台 PUT /users/{id} 解耦 —— role / is_active /
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


@router.post("/users", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest,
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """创建用户（仅 Admin）"""
    # 检查用户名是否已存在
    existing = await user_repo.get_by_username(request.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{request.username}' already exists")

    if request.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'user'")

    # 校验 department_id 引用合法（DB FK 也会兜，但前置给出友好错误）
    if request.department_id is not None:
        dept = await dept_repo.get_by_id(request.department_id)
        if dept is None:
            raise HTTPException(status_code=400, detail="department_id does not reference an existing department")

    user = User(
        id=f"user-{uuid4().hex}",
        username=request.username,
        hashed_password=hash_password(request.password),
        display_name=request.display_name,
        role=request.role,
        department_id=request.department_id,
    )
    await user_repo.add(user)

    logger.info(f"User created: {user.username} (role={user.role})")

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        department_id=user.department_id,
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
                department_id=u.department_id,
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ],
        total=total,
    )


# ============================================================
# Bulk Actions (PR5a)
# ============================================================
#
# 路由注册顺序约束：`GET /users/bulk-impact` 必须早于 `GET /users/{user_id}`
# 注册，否则会被解析为 user_id="bulk-impact"。POST /users/bulk-action 没有
# 对应的 POST /users/{id} 路由，方法层面不会撞，但放在这里方便就近聚类。


@router.get("/users/bulk-impact", response_model=BulkImpactResponse)
async def get_users_bulk_impact(
    ids: list[str] = Query(..., min_length=1, max_length=MAX_BULK_USER_ACTION_IDS),
    _admin: TokenPayload = Depends(require_admin),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    批量删除用户前的影响数据 — 一次 IN 查询。

    给前端 DangerConfirmModal 显示"将删除 N 个用户、共 M 条会话"。
    user_count 是请求 ids 的去重数（不区分是否真存在），conversation_count
    是这批用户名下当前会话总数。
    """
    unique_ids = list({i for i in ids if i})
    count = await conversation_manager.count_users_conversations(unique_ids)
    return BulkImpactResponse(
        user_count=len(unique_ids),
        conversation_count=count,
    )


@router.post("/users/bulk-action", response_model=BulkActionResponse)
async def bulk_user_action(
    request: BulkActionRequest,
    current_user: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    批量执行用户管理动作（仅 Admin）。

    支持 action：disable / enable / delete / set_department。Best-effort：
    单条失败不阻断其余。Self-protection 取代 last-admin 计数 —— admin
    不能对自己执行任何 action（与 PR2b 单条 update_user / delete_user 一致）；
    配合 disabled admin 进不来后台，足以保住至少 1 个活跃 admin。

    set_department 的 payload.department_id 在 loop 外预校验：非 null 且
    在 DB 中找不到对应部门 → 整批 400（fail-fast，省掉无谓的 N 次失败）。

    delete 走 hard_delete（DB CASCADE）。若用户当前正在跑 engine，
    被级联删的 conversation 行由 PR2a 的 controller exists() / IntegrityError
    catch 兜住；本端点直接 fire-and-forget。
    """
    # set_department 预校验：非 null department_id 必须存在
    target_dept_id: Optional[str] = None
    if request.action == "set_department":
        payload = request.payload or {}
        if "department_id" not in payload:
            raise HTTPException(
                status_code=400,
                detail="set_department requires payload.department_id (use null to clear)",
            )
        target_dept_id = payload["department_id"]
        if target_dept_id is not None:
            if not isinstance(target_dept_id, str):
                raise HTTPException(
                    status_code=400,
                    detail="payload.department_id must be a string or null",
                )
            dept = await dept_repo.get_by_id(target_dept_id)
            if dept is None:
                raise HTTPException(
                    status_code=400,
                    detail="department_id does not reference an existing department",
                )

    succeeded: list[str] = []
    failed: list[BulkActionFailedItem] = []
    seen: set[str] = set()

    for user_id in request.ids:
        if user_id in seen:
            continue
        seen.add(user_id)

        if user_id == current_user.user_id:
            failed.append(BulkActionFailedItem(id=user_id, reason="forbidden_self"))
            continue

        try:
            if request.action == "delete":
                deleted = await user_repo.hard_delete(user_id)
                if deleted:
                    succeeded.append(user_id)
                else:
                    failed.append(BulkActionFailedItem(id=user_id, reason="not_found"))
                continue

            user = await user_repo.get_by_id(user_id)
            if user is None:
                failed.append(BulkActionFailedItem(id=user_id, reason="not_found"))
                continue

            if request.action == "disable":
                user.is_active = False
            elif request.action == "enable":
                user.is_active = True
            elif request.action == "set_department":
                user.department_id = target_dept_id
            else:
                # Pydantic Literal 兜底，理论上到不了这里
                failed.append(BulkActionFailedItem(id=user_id, reason="internal_error"))
                continue

            await user_repo.update(user)
            succeeded.append(user_id)
        except Exception as e:
            logger.warning(f"bulk_user_action failed for {user_id}: {e}")
            failed.append(BulkActionFailedItem(id=user_id, reason="internal_error"))

    logger.info(
        f"Bulk action '{request.action}' done: succeeded={len(succeeded)} "
        f"failed={len(failed)}"
    )

    return BulkActionResponse(succeeded=succeeded, failed=failed)


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
        department_id=user.department_id,
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
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    更新用户（仅 Admin）

    防误锁：admin 不能改自己的 role / is_active / password。配合 DELETE
    路径的"不能删自己"保护，足以保证系统始终至少有 1 个活跃 admin
    （操作者必然活跃 → 不能动自己 → 至少剩自己）。

    Self 改 password 走 POST /me/password —— 该端点强制校验 current_password，
    防止 token 被盗后攻击者无需旧密码就能接管账号。本端点对 self 改 password
    返回 403，避免在 admin 后台绕过该校验。
    """
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    is_self = user_id == current_user.user_id

    if request.display_name is not None:
        user.display_name = request.display_name or None
    if request.password is not None:
        if is_self:
            raise HTTPException(
                status_code=403,
                detail="Use POST /auth/me/password to change your own password",
            )
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

    # department_id 用 model_fields_set 区分"未传"与"显式 null（清空）"
    if "department_id" in request.model_fields_set:
        if request.department_id is not None:
            dept = await dept_repo.get_by_id(request.department_id)
            if dept is None:
                raise HTTPException(
                    status_code=400,
                    detail="department_id does not reference an existing department",
                )
        user.department_id = request.department_id

    await user_repo.update(user)

    logger.info(f"User updated: {user.username}")

    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        department_id=user.department_id,
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


# ============================================================
# Bulk Import (PR3)
# ============================================================


def _validate_dept_path(row: ParsedRow) -> Optional[list[str]]:
    """
    校验部门字段必须是连续前缀（dept_l1 → dept_l2 → dept_l3，无 gap）。

    返回非空 path 列表（顶层 → 末级）；全空 → None；gap → 抛 ValueError。

    设计理由：resolve_department_path 在内部会折叠空段（[A,'',C] → [A,C]），
    对 cascader 提交是合理的（用户主动选了哪一级就是哪一级），但 CSV 用户
    可能误把"想跳过 l2"理解成"l1 直接当 l2 父" —— gap 在 CSV 语义里是错。
    importer 自己做严格校验，把陷阱挡在调用 resolve 之前。
    """
    levels = [row.dept_l1, row.dept_l2, row.dept_l3]
    # 找最后一个非空的层级 — 之后的位置都必须为空（前缀语义）
    last_non_empty = -1
    for i, v in enumerate(levels):
        if v:
            last_non_empty = i
    if last_non_empty < 0:
        return None
    # 前 last_non_empty 个位置必须都非空
    for i in range(last_non_empty + 1):
        if not levels[i]:
            raise ValueError(
                f"department levels must be contiguous (dept_l{i+1} empty "
                f"but a deeper level is set)"
            )
    return levels[: last_non_empty + 1]


def _validate_field_lengths(row: ParsedRow) -> None:
    """
    校验 CSV 字段长度不超 DB 列宽 / Pydantic schema max_length。

    普通 API 走 schema 自动拦下；CSV 路径绕过 schema，必须在 importer 里
    复刻这层校验，否则 PG/MySQL 在 INSERT 阶段会炸 500（SQLite VARCHAR
    不强制长度，所以 SQLite 测试发现不了这个问题）。

    注意 username 不在此处校验 —— validate_username 已限定 2-64 ASCII。

    Raises:
        ValueError: 任一字段超长，message 为具体字段名 + 实际长度 + 上限。
    """
    if len(row.display_name) > DISPLAY_NAME_MAX:
        raise ValueError(
            f"display_name too long: {len(row.display_name)} chars (max {DISPLAY_NAME_MAX})"
        )
    # password 仅在 explicit 时校验 —— 默认 = username 时长度一定 ≤ 64
    if row.password and len(row.password) > PASSWORD_MAX:
        raise ValueError(
            f"password too long: {len(row.password)} chars (max {PASSWORD_MAX})"
        )
    for level_idx, name in enumerate((row.dept_l1, row.dept_l2, row.dept_l3), start=1):
        if name and len(name) > DEPT_NAME_MAX:
            raise ValueError(
                f"dept_l{level_idx} too long: {len(name)} chars (max {DEPT_NAME_MAX})"
            )


@router.post("/users/bulk-import", response_model=BulkImportResponse)
async def bulk_import_users(
    file: UploadFile = File(...),
    _admin: TokenPayload = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
    dept_repo: DepartmentRepository = Depends(get_department_repository),
):
    """
    批量导入用户（仅 Admin）。

    CSV header 必含 `username`；可选列 `password` / `display_name` /
    `dept_l1` / `dept_l2` / `dept_l3`。其他列被忽略并在 warnings 里上报。

    语义（best-effort，非原子）：
    - parse 阶段失败（解码 / 缺 username 列 / 行数超限）→ 400
    - 文件内 username 重复 → 400 + duplicate_rows 列出
    - 单行业务校验失败（username 格式 / 部门 gap / 字段超长 / 默认密码过短）
      → failed
    - 单行 username 已在 DB → skipped
    - 其余 → created（每行独立 commit；逐行成功/失败）

    执行结构（3 段）：
    1. validate + dept resolve：顺序遍历，校验失败的行进 failed，
       通过的行收集到 to_create（含已 resolve 的 department_id）。
       dept_cache 避免同 CSV 内重复路径多次 SELECT。
    2. parallel hash：to_create 里所有 password 通过 asyncio.gather +
       asyncio.to_thread 并行扔给默认 ThreadPoolExecutor 跑 bcrypt。
       bcrypt-python 在 C 层释放 GIL，~8 核机器 300 行 hash 阶段
       从 ~50s 缩到 ~6s，且 event loop 全程不卡（其他请求正常响应）。
    3. INSERT：顺序写库；username UNIQUE 撞上 → 当 skipped 处理（race
       between phase-1 batch-check 和此处之间另一 admin 抢先创建）。
    """
    raw = await file.read()
    if len(raw) > config.MAX_BULK_IMPORT_BYTES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"File too large: {len(raw) / 1024 / 1024:.1f}MB "
                f"(max {config.MAX_BULK_IMPORT_BYTES / 1024 / 1024:.0f}MB)"
            ),
        )

    try:
        parsed = parse_user_csv(raw, max_rows=config.MAX_BULK_IMPORT_ROWS)
    except CsvParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if parsed.duplicate_rows:
        # 文件内重复 — 整体拒绝（admin 必须先在源文件去重，否则容易把 password
        # 列后写的覆盖前面的，安全/可预测性都差）
        raise HTTPException(
            status_code=400,
            detail={
                "message": "CSV contains duplicate usernames within the file",
                "duplicate_rows": [
                    {"row": r, "username": u} for r, u in parsed.duplicate_rows
                ],
            },
        )

    total_rows = len(parsed.rows)
    warnings = list(parsed.warnings)

    # 批查已存在的 username（一次性 SELECT IN，避免 N+1）
    candidate_usernames = {r.username for r in parsed.rows if r.username}
    existing = await user_repo.find_existing_usernames(candidate_usernames)

    created: list[UserResponse] = []
    failed: list[BulkImportFailedRow] = []
    skipped: list[BulkImportSkippedRow] = []

    # 部门路径 cache（同 CSV 内重复路径只 resolve 一次）
    dept_cache: dict[tuple[str, ...], Optional[str]] = {}

    # ---------- Phase 1: validate + dept resolve ----------
    # 收集通过校验的行；password 此时只挑出来不 hash
    to_create: list[tuple[ParsedRow, str, Optional[str]]] = []  # (row, password, dept_id)

    for row in parsed.rows:
        # username 必填 + 格式
        if not row.username:
            failed.append(BulkImportFailedRow(
                row=row.row_number, username=None,
                reason="username is required",
            ))
            continue
        try:
            validate_username(row.username)
        except ValueError as e:
            failed.append(BulkImportFailedRow(
                row=row.row_number, username=row.username, reason=str(e),
            ))
            continue

        # 已存在 → skipped
        if row.username in existing:
            skipped.append(BulkImportSkippedRow(
                row=row.row_number, username=row.username,
                reason="username_exists",
            ))
            continue

        # 字段长度（display_name / password / dept_l*）— 复刻 schema max_length
        try:
            _validate_field_lengths(row)
        except ValueError as e:
            failed.append(BulkImportFailedRow(
                row=row.row_number, username=row.username, reason=str(e),
            ))
            continue

        # 默认密码 = username；强制最短 4 位（与 CreateUserRequest 一致）
        password = row.password if row.password else row.username
        if len(password) < 4:
            failed.append(BulkImportFailedRow(
                row=row.row_number, username=row.username,
                reason=(
                    f"password too short (min 4 chars); "
                    f"default = username '{row.username}' has only {len(row.username)} chars"
                ),
            ))
            continue

        # 部门路径：gap 校验 + cache + resolve
        try:
            path = _validate_dept_path(row)
        except ValueError as e:
            failed.append(BulkImportFailedRow(
                row=row.row_number, username=row.username, reason=str(e),
            ))
            continue

        department_id: Optional[str] = None
        if path is not None:
            cache_key = tuple(seg.strip() for seg in path)
            if cache_key in dept_cache:
                department_id = dept_cache[cache_key]
            else:
                department_id = await resolve_department_path(dept_repo, path)
                dept_cache[cache_key] = department_id

        to_create.append((row, password, department_id))
        # phase-1 内同 CSV 后续行不再尝试同 username（防 inter-row 冲突）
        existing.add(row.username)

    # ---------- Phase 2: parallel hash ----------
    # bcrypt 是 CPU bound；丢线程池并行跑 + 释放 event loop。
    # gather 失败（极罕见）→ 整体 500 是正确行为（系统级问题，不静默吞）。
    hashed_passwords: list[str] = (
        await asyncio.gather(*(
            asyncio.to_thread(hash_password, pw) for _, pw, _ in to_create
        ))
        if to_create else []
    )

    # ---------- Phase 3: INSERT ----------
    for (row, _password, dept_id), hashed in zip(to_create, hashed_passwords):
        new_user = User(
            id=f"user-{uuid4().hex}",
            username=row.username,
            hashed_password=hashed,
            display_name=row.display_name or None,
            role="user",
            department_id=dept_id,
        )
        try:
            await user_repo.add(new_user)
        except IntegrityError:
            # phase-1 batch check 与此处之间另一 admin 抢先创建了同名 username
            await user_repo.session.rollback()
            skipped.append(BulkImportSkippedRow(
                row=row.row_number, username=row.username,
                reason="username_exists",
            ))
            continue

        created.append(UserResponse(
            id=new_user.id,
            username=new_user.username,
            display_name=new_user.display_name,
            role=new_user.role,
            is_active=new_user.is_active,
            department_id=new_user.department_id,
            created_at=new_user.created_at,
            updated_at=new_user.updated_at,
        ))

    logger.info(
        f"Bulk import done: total={total_rows} created={len(created)} "
        f"failed={len(failed)} skipped={len(skipped)}"
    )

    return BulkImportResponse(
        created=created,
        failed=failed,
        skipped=skipped,
        total_rows=total_rows,
        detected_encoding=parsed.detected_encoding,
        warnings=warnings,
    )
