"""
Auth-related Pydantic schemas

Defines request and response models for authentication endpoints.
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


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


class UpdateUserRequest(BaseModel):
    """PUT /api/v1/auth/users/{id} request body"""
    display_name: Optional[str] = Field(None, max_length=128, description="Display name")
    password: Optional[str] = Field(None, min_length=4, max_length=128, description="New password")
    role: Optional[str] = Field(None, description="Role (admin or user)")
    is_active: Optional[bool] = Field(None, description="Whether user is active")


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
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """GET /api/v1/auth/users response"""
    users: List[UserResponse]
    total: int
