"""
Artifact-related Pydantic schemas

Defines request and response models for artifact endpoints.
"""

from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, Field


# ============================================================
# Response Models
# ============================================================

class ArtifactSummary(BaseModel):
    """Artifact summary in list response"""
    id: str = Field(..., description="Artifact ID")
    content_type: str = Field(..., description="Content type (MIME type, e.g. text/markdown, text/x-python)")
    title: str = Field(..., description="Artifact title")
    current_version: int = Field(..., description="Current version number")
    source: Optional[str] = Field(None, description="Source (agent, user_upload)")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")


class ArtifactListResponse(BaseModel):
    """GET /api/v1/artifacts/{session_id} response"""
    session_id: str = Field(..., description="Session ID")
    artifacts: List[ArtifactSummary] = Field(..., description="Artifact list")


class VersionSummary(BaseModel):
    """Version summary in version list response"""
    version: int = Field(..., description="Version number")
    update_type: str = Field(..., description="Update type (create, update, update_fuzzy, rewrite)")
    created_at: datetime = Field(..., description="Version creation time")


class VersionDetailResponse(BaseModel):
    """GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} response"""
    version: int = Field(..., description="Version number")
    content: str = Field(..., description="Version content")
    update_type: str = Field(..., description="Update type")
    changes: Optional[List[List[str]]] = Field(None, description="Changes [[old, new], ...]")
    created_at: datetime = Field(..., description="Version creation time")


class VersionListResponse(BaseModel):
    """GET /api/v1/artifacts/{session_id}/{artifact_id}/versions response"""
    artifact_id: str = Field(..., description="Artifact ID")
    session_id: str = Field(..., description="Session ID")
    versions: List[VersionSummary] = Field(..., description="Version list")


class ArtifactDetailResponse(BaseModel):
    """GET /api/v1/artifacts/{session_id}/{artifact_id} response"""
    id: str = Field(..., description="Artifact ID")
    session_id: str = Field(..., description="Session ID")
    content_type: str = Field(..., description="Content type")
    title: str = Field(..., description="Artifact title")
    content: str = Field(..., description="Current version content")
    current_version: int = Field(..., description="Current version number")
    source: Optional[str] = Field(None, description="Source (agent, user_upload)")
    created_at: datetime = Field(..., description="Creation time")
    updated_at: datetime = Field(..., description="Last update time")
    versions: List[VersionSummary] = Field(default_factory=list, description="All version summaries")
    latest_version: Optional[VersionDetailResponse] = Field(None, description="Latest version detail (content + changes)")


class UploadResponse(BaseModel):
    """POST /api/v1/artifacts/{session_id}/upload response"""
    id: str = Field(..., description="Artifact ID")
    session_id: str = Field(..., description="Session ID")
    content_type: str = Field(..., description="MIME type (after conversion)")
    title: str = Field(..., description="Artifact title")
    current_version: int = Field(..., description="Current version number")
    source: str = Field(..., description="Source (user_upload)")
    original_filename: str = Field(..., description="Original uploaded filename")
    created_at: datetime = Field(..., description="Creation time")
