"""Skill 用户侧管理 schemas(C-3):列可见 skill + 个人 enable/disable toggle。"""

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


class SkillListResponse(BaseModel):
    """GET /api/v1/skills response."""
    skills: List[SkillItem] = Field(
        ..., description="Skills visible to the user, with effective enabled state."
    )


class SkillToggleRequest(BaseModel):
    """PUT /api/v1/skills/{slug}/enabled request body."""
    enabled: bool = Field(..., description="Personal enable/disable override for this skill.")
