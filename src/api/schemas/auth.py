"""
Auth-related Pydantic schemas

Defines request and response models for authentication endpoints.
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

from utils.validators import validate_username


# ============================================================
# Request Models
# ============================================================

class LoginRequest(BaseModel):
    """POST /api/v1/auth/login request body"""
    username: str = Field(..., description="Username")
    password: str = Field(..., description="Password")


class CreateUserRequest(BaseModel):
    """POST /api/v1/auth/users request body"""
    username: str = Field(..., min_length=2, max_length=64, description="Username")
    password: str = Field(..., min_length=4, max_length=128, description="Password")
    display_name: Optional[str] = Field(None, max_length=128, description="Display name")
    role: str = Field("user", description="Role (admin or user)")
    department_id: Optional[str] = Field(None, description="Department id; null = unassigned")

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        validate_username(v)
        return v


class UpdateUserRequest(BaseModel):
    """
    PUT /api/v1/auth/users/{id} request body.

    All fields optional. department_id semantics: 字段被显式传入时生效（包括
    传 null = 清空归属）；字段缺省 → 不改。路由通过 model_fields_set 区分。
    """
    display_name: Optional[str] = Field(None, max_length=128, description="Display name")
    password: Optional[str] = Field(None, min_length=4, max_length=128, description="New password")
    role: Optional[str] = Field(None, description="Role (admin or user)")
    is_active: Optional[bool] = Field(None, description="Whether user is active")
    department_id: Optional[str] = Field(None, description="Department id; explicit null clears")


class ChangePasswordRequest(BaseModel):
    """POST /api/v1/auth/me/password request body"""
    current_password: str = Field(..., min_length=1, max_length=128, description="Current password")
    new_password: str = Field(..., min_length=4, max_length=128, description="New password")


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
    """GET /api/v1/auth/users response"""
    users: List[UserResponse]
    total: int


class UserImpactResponse(BaseModel):
    """
    GET /api/v1/auth/users/{id}/impact response

    给前端硬删用户前的二次确认弹窗显示影响数据。
    """
    conversation_count: int = Field(..., description="该用户拥有的对话数（CASCADE 删除时一并丢失）")
