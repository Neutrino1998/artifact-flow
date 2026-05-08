"""
Department-related Pydantic schemas.

部门 CRUD + 树查询 + 路径解析的请求/响应模型。
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


# ============================================================
# Request Models
# ============================================================

class CreateDepartmentRequest(BaseModel):
    """POST /api/v1/departments request body"""
    name: str = Field(..., min_length=1, max_length=128, description="Department name")
    parent_id: Optional[str] = Field(None, description="Parent department id; null = top-level")

    @field_validator("name")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped


class UpdateDepartmentRequest(BaseModel):
    """PATCH /api/v1/departments/{id} request body — 仅改名"""
    name: str = Field(..., min_length=1, max_length=128, description="New department name")

    @field_validator("name")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name cannot be empty or whitespace-only")
        return stripped


class MoveDepartmentRequest(BaseModel):
    """POST /api/v1/departments/{id}/move request body — 搬家"""
    new_parent_id: Optional[str] = Field(None, description="New parent id; null = move to root")


class ResolveDepartmentRequest(BaseModel):
    """POST /api/v1/departments/resolve request body"""
    path: List[str] = Field(..., description="Department path from top to leaf; empty list → null id")


# ============================================================
# Response Models
# ============================================================

class DepartmentResponse(BaseModel):
    """Single department response (含直属计数)"""
    id: str
    parent_id: Optional[str] = None
    name: str
    user_count: int = Field(..., description="该部门下直属用户数（不含子部门）")
    child_count: int = Field(..., description="直接子部门数")
    created_at: datetime
    updated_at: datetime


class DepartmentTreeNode(BaseModel):
    """Tree node — 递归结构，包含 children"""
    id: str
    parent_id: Optional[str] = None
    name: str
    user_count: int = Field(..., description="该部门下直属用户数")
    children: List["DepartmentTreeNode"] = Field(default_factory=list)


class DepartmentTreeResponse(BaseModel):
    """GET /api/v1/departments/tree response — 顶层节点列表"""
    nodes: List[DepartmentTreeNode]


class DepartmentListResponse(BaseModel):
    """GET /api/v1/departments response — 同级列表"""
    departments: List[DepartmentResponse]


class ResolveDepartmentResponse(BaseModel):
    """POST /api/v1/departments/resolve response"""
    id: Optional[str] = Field(None, description="末级部门 id；空 path → null")


# Pydantic v2 forward-ref resolution for self-referencing model
DepartmentTreeNode.model_rebuild()
