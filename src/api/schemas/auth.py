"""
Auth-related Pydantic schemas

Defines request and response models for authentication endpoints.
"""

from typing import Any, Dict, Literal, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from utils.password_policy import validate_password_strength
from utils.validators import validate_username


MAX_BULK_USER_ACTION_IDS = 200


# ============================================================
# Request Models
# ============================================================

class LoginRequest(BaseModel):
    """POST /api/v1/auth/login request body"""
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class CreateUserRequest(BaseModel):
    """POST /api/v1/admin/users request body"""
    username: str = Field(..., min_length=2, max_length=64, description="Username")
    # 长度下限/复杂度交给 validate_password_strength（config 驱动）,Field 只兜 max。
    password: str = Field(..., max_length=128, description="Password")
    display_name: Optional[str] = Field(None, max_length=128, description="Display name")
    role: str = Field("user", description="Role (admin or user)")
    department_id: Optional[str] = Field(None, description="Department id; null = unassigned")

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        validate_username(v)
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


class UpdateUserRequest(BaseModel):
    """
    PUT /api/v1/admin/users/{id} request body.

    All fields optional. department_id semantics: 字段被显式传入时生效（包括
    传 null = 清空归属）；字段缺省 → 不改。路由通过 model_fields_set 区分。
    """
    display_name: Optional[str] = Field(None, max_length=128, description="Display name")
    password: Optional[str] = Field(None, max_length=128, description="New password")
    role: Optional[str] = Field(None, description="Role (admin or user)")
    is_active: Optional[bool] = Field(None, description="Whether user is active")
    department_id: Optional[str] = Field(None, description="Department id; explicit null clears")

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: Optional[str]) -> Optional[str]:
        # 缺省（不改密码）跳过;显式传入才校验强度。
        if v is None:
            return v
        validate_password_strength(v)
        return v


class ChangePasswordRequest(BaseModel):
    """POST /api/v1/auth/me/password request body"""
    current_password: str = Field(..., min_length=1, max_length=128, description="Current password")
    new_password: str = Field(..., max_length=128, description="New password")

    @field_validator("new_password")
    @classmethod
    def _check_new_password(cls, v: str) -> str:
        validate_password_strength(v)
        return v


class UpdateMyProfileRequest(BaseModel):
    """PATCH /api/v1/auth/me request body — 自助修改自己的非敏感资料字段"""
    display_name: Optional[str] = Field(None, max_length=128, description="Display name; pass empty string to clear")


# ============================================================
# Response Models
# ============================================================

class UserInfo(BaseModel):
    """User info in responses"""
    id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    display_name: Optional[str] = Field(None, description="Display name")
    role: str = Field(..., description="Role")
    must_change_password: bool = Field(
        False,
        description=(
            "True 时前端须强制弹出改密框,且除改密/登出外的请求会被后端 403 "
            "(首次登录 / 管理员重置 / 口令到期)。改密成功后清除。"
        ),
    )
    department_path: Optional[List[str]] = Field(
        None,
        description=(
            "Names of the user's department ancestors, root → leaf. "
            "None when the user has no department. Sidebar shows the leaf; "
            "future UIs can render the full chain without a second request."
        ),
    )


class LoginResponse(BaseModel):
    """POST /api/v1/auth/login response"""
    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field("bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiry in seconds")
    user: UserInfo = Field(..., description="User info")


class UserResponse(BaseModel):
    """Single user response (admin)"""
    id: str
    username: str
    display_name: Optional[str] = None
    role: str
    is_active: bool
    department_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """GET /api/v1/admin/users response"""
    users: List[UserResponse]
    total: int


class UserImpactResponse(BaseModel):
    """
    GET /api/v1/admin/users/{id}/impact response

    给前端硬删用户前的二次确认弹窗显示影响数据。
    """
    conversation_count: int = Field(..., description="该用户拥有的对话数（CASCADE 删除时一并丢失）")


# ============================================================
# Bulk Import (PR3)
# ============================================================


class BulkImportFailedRow(BaseModel):
    """单行业务校验失败 — 行号 + username（可能为空）+ 原因。"""
    row: int = Field(..., description="1-based data row number (excluding header)")
    username: Optional[str] = Field(None, description="Username on the row, may be empty if row had none")
    reason: str = Field(..., description="Failure reason (validation message)")


class BulkImportSkippedRow(BaseModel):
    """单行被跳过 — 当前唯一原因是 username 已在 DB 中存在。"""
    row: int
    username: str
    reason: str = Field("username_exists", description="Skip reason")


class BulkImportResponse(BaseModel):
    """
    POST /api/v1/admin/users/bulk-import response。

    best-effort 三分类。total_rows = created + failed + skipped。
    warnings 含编码 fallback / unknown 列等非阻断提示。
    """
    created: List[UserResponse] = Field(default_factory=list)
    failed: List[BulkImportFailedRow] = Field(default_factory=list)
    skipped: List[BulkImportSkippedRow] = Field(default_factory=list)
    total_rows: int = Field(..., description="Total data rows processed (excluding header)")
    detected_encoding: Optional[str] = Field(None, description="Encoding charset-normalizer picked")
    warnings: List[str] = Field(default_factory=list, description="Non-blocking notices (unknown columns, etc.)")


# ============================================================
# Bulk Actions (PR5a)
# ============================================================


BulkActionType = Literal["disable", "enable", "delete", "set_department"]


class BulkActionRequest(BaseModel):
    """
    POST /api/v1/admin/users/bulk-action request body。

    payload 仅在 action="set_department" 时使用，shape = {"department_id": str | null}；
    null 表示清空归属。其他 action 忽略 payload。
    """
    ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_BULK_USER_ACTION_IDS,
        description=f"User IDs (1-{MAX_BULK_USER_ACTION_IDS})",
    )
    action: BulkActionType = Field(..., description="Action to apply to all listed users")
    payload: Optional[Dict[str, Any]] = Field(
        None,
        description="Action-specific payload (set_department: {department_id: str|null})",
    )


class BulkActionFailedItem(BaseModel):
    """单条 bulk-action 失败项。"""
    id: str = Field(..., description="User ID that failed")
    reason: str = Field(
        ..., description="Failure reason: 'forbidden_self' | 'not_found' | 'internal_error'"
    )


class BulkActionResponse(BaseModel):
    """POST /api/v1/admin/users/bulk-action response."""
    succeeded: List[str] = Field(default_factory=list, description="User IDs successfully processed")
    failed: List[BulkActionFailedItem] = Field(default_factory=list)


class BulkImpactResponse(BaseModel):
    """
    GET /api/v1/admin/users/bulk-impact response。

    给前端 DangerConfirmModal 显示"将删除 N 个用户、共 M 条会话"。
    user_count = 请求 ids 的去重个数（不区分是否真正存在）；
    conversation_count = 这批用户名下当前会话总数（CASCADE 级联会丢失的）。
    """
    user_count: int = Field(..., description="Number of distinct user IDs in the request")
    conversation_count: int = Field(..., description="Total conversations across these users")
