"""Skill 管理 schemas:列可见 skill + 个人 toggle(C-3),导入/导出/删除(E-2)。"""

from typing import List
from pydantic import BaseModel, Field


class SkillItem(BaseModel):
    """一个对用户可见的 skill + 其有效启用态。"""
    slug: str = Field(..., description="Skill slug (natural key)")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="One-line description (the L1 index text)")
    enabled: bool = Field(
        ...,
        description=(
            "Effective enabled state (config default overridden by the user's setting). "
            "Controls whether the skill enters the model's L1 <available_skills> index. A "
            "disabled skill is still visible and can be activated on demand via its button."
        ),
    )
    default_enabled: bool = Field(
        ...,
        description="Config-seed default; shown so the UI can flag skills the user overrode.",
    )
    is_overridden: bool = Field(
        ...,
        description="Whether the user has an explicit personal enable/disable for this skill.",
    )
    source: str = Field(
        ...,
        description=(
            "'seeded' (config-owned, read-only in the UI) or 'dynamic' (imported via the "
            "UI; deletable by its owner or an admin)."
        ),
    )
    has_bundle: bool = Field(
        ...,
        description="Whether an original zip bundle exists (enables lossless export).",
    )
    visibility: str = Field(
        ..., description="private (owner-only) | public (shared) | department"
    )
    is_owner: bool = Field(
        ..., description="Whether the current user imported (owns) this dynamic skill."
    )


class SkillListResponse(BaseModel):
    """GET /api/v1/skills response."""
    skills: List[SkillItem] = Field(
        ..., description="Skills visible to the user, with effective enabled state."
    )


class SkillToggleRequest(BaseModel):
    """PUT /api/v1/skills/{slug}/enabled request body."""
    enabled: bool = Field(..., description="Personal enable/disable override for this skill.")


class FindingItem(BaseModel):
    """一条 validator finding(E-1 硬门产出;rule id 稳定,前端按 severity 渲染)。"""
    rule: str = Field(..., description="Stable rule id, e.g. 'zip.invalid' / 'md.body_empty'.")
    severity: str = Field(..., description="'error' (rejected) or 'warning' (surfaced only).")
    message: str = Field(..., description="Human-readable explanation.")


class SkillImportResponse(BaseModel):
    """POST /api/v1/skills/import(+ admin 变体)成功响应。硬门拒收走 422,
    detail = {message, findings[]} 同一 FindingItem 形状。"""
    status: str = Field(..., description="Always 'imported' on success.")
    skill: SkillItem = Field(..., description="The imported skill as it appears in the list.")
    findings: List[FindingItem] = Field(
        ..., description="Non-blocking warnings surfaced by the validator (may be empty)."
    )
