"""Schemas for admin department access management (Phase G-1)."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DepartmentAccessDepartment(BaseModel):
    id: str
    parent_id: Optional[str] = None
    name: str


class DepartmentAccessInheritedRule(BaseModel):
    department_id: str = Field(..., description="Ancestor department id")
    department_name: str = Field(..., description="Ancestor department display name")


class DepartmentSkillAccessItem(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    visibility: Literal["public", "department"]
    source: str
    default_enabled: bool
    rule_action: Literal["grant", "deny"] = Field(
        ...,
        description="'deny' for public resources, 'grant' for department resources.",
    )
    direct_rule: bool = Field(
        ..., description="Whether this exact department has an exception row."
    )
    inherited_rule: Optional[DepartmentAccessInheritedRule] = Field(
        None, description="Nearest ancestor exception row, if any."
    )
    effective_allowed: bool = Field(
        ..., description="Whether users in this department can see/use this resource."
    )


class DepartmentUnitAccessItem(BaseModel):
    name: str
    kind: str
    description: str
    visibility: Literal["public", "department"]
    source: str
    rule_action: Literal["grant", "deny"]
    direct_rule: bool
    inherited_rule: Optional[DepartmentAccessInheritedRule] = None
    effective_allowed: bool


class DepartmentAccessResponse(BaseModel):
    department: DepartmentAccessDepartment
    skills: List[DepartmentSkillAccessItem]
    units: List[DepartmentUnitAccessItem]
