"""Skill 管理 schemas:用户侧 skill 管理 + admin 共享 skill 管理。"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class SkillItem(BaseModel):
    """一个对用户可见的 skill + 其有效启用态。"""
    id: str = Field(..., description="Stable skill id used by management APIs")
    slug: str = Field(..., description="User-context skill slug")
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
    has_extra_files: bool = Field(
        ...,
        description=(
            "Whether the skill bundle contains files beyond SKILL.md. Controls whether "
            "read_skill points to mount_skill."
        ),
    )
    visibility: str = Field(
        ..., description="private (owner-only) | public (shared) | department"
    )
    is_owner: bool = Field(
        ..., description="Whether the current user imported (owns) this dynamic skill."
    )
    shadowed_by_private: bool = Field(
        ...,
        description=(
            "Whether this shared skill is currently hidden at runtime by the user's "
            "same-slug private skill."
        ),
    )


class SkillListResponse(BaseModel):
    """GET /api/v1/skills response."""
    skills: List[SkillItem] = Field(
        ..., description="Skills visible to the user, with effective enabled state."
    )


class SkillDetailResponse(BaseModel):
    """On-demand SKILL.md preview for one management-list item."""
    id: str = Field(..., description="Stable skill id")
    slug: str = Field(..., description="User-context skill slug")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="One-line description")
    skill_md: str = Field(
        ...,
        description="SKILL.md guidance body with YAML frontmatter removed",
    )
    source: Literal["seeded", "dynamic"] = Field(
        ..., description="Config-owned or UI-imported source"
    )
    visibility: Literal["private", "public", "department"] = Field(
        ..., description="Effective catalog visibility"
    )
    has_extra_files: bool = Field(
        ..., description="Whether the downloadable bundle contains files beyond SKILL.md"
    )


class SkillToggleRequest(BaseModel):
    """PUT /api/v1/skills/{skill_id}/enabled request body."""
    enabled: bool = Field(..., description="Personal enable/disable override for this skill.")


class FindingItem(BaseModel):
    """一条 validator finding；rule id 稳定，前端按 severity 渲染。"""
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


class AdminSkillItem(BaseModel):
    """Admin shared skill catalog item (not filtered by the admin user's department)."""
    id: str = Field(..., description="Stable skill id used by management APIs")
    slug: str = Field(..., description="Shared-catalog slug")
    name: str = Field(..., description="Display name")
    description: str = Field(..., description="One-line description")
    visibility: Literal["public", "department"] = Field(
        ..., description="public (default allow) or department (default deny)"
    )
    default_enabled: bool = Field(
        ..., description="Whether this shared skill enters users' L1 index by default"
    )
    source: Literal["seeded", "dynamic"] = Field(
        ..., description="seeded skills are config-owned; dynamic shared skills are editable"
    )
    has_extra_files: bool = Field(
        ..., description="Whether the skill bundle contains files beyond SKILL.md"
    )
    can_edit: bool = Field(
        ..., description="True only for dynamic shared skills"
    )


class AdminSkillListResponse(BaseModel):
    """GET /api/v1/admin/skills response."""
    skills: List[AdminSkillItem] = Field(
        ..., description="All shared skills, including seeded read-only ones"
    )


class AdminSkillUpdateRequest(BaseModel):
    """PATCH /api/v1/admin/skills/{skill_id} request body."""
    visibility: Optional[Literal["public", "department"]] = Field(
        None, description="New shared visibility. Changing it clears department rules."
    )
    default_enabled: Optional[bool] = Field(
        None, description="New default L1 enabled state for users without an override."
    )
