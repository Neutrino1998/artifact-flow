"""
Artifacts Router

处理 Artifact 相关的 API 端点：
- GET /api/v1/artifacts/{session_id} - 列出 artifacts
- GET /api/v1/artifacts/{session_id}/{artifact_id} - 获取详情
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions - 版本列表
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} - 特定版本
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_artifact_manager
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactDetailResponse,
    ArtifactSummary,
    VersionListResponse,
    VersionDetailResponse,
    VersionSummary,
)
from tools.implementations.artifact_ops import ArtifactManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/{session_id}", response_model=ArtifactListResponse)
async def list_artifacts(
    session_id: str,
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    列出 session 下所有 artifacts
    """
    try:
        artifacts = await artifact_manager.list_artifacts(
            session_id=session_id,
            include_content=False
        )

        return ArtifactListResponse(
            session_id=session_id,
            artifacts=[
                ArtifactSummary(
                    id=art["id"],
                    content_type=art["content_type"],
                    title=art["title"],
                    current_version=art["version"],
                    created_at=datetime.fromisoformat(art["created_at"]) if isinstance(art["created_at"], str) else art["created_at"],
                    updated_at=datetime.fromisoformat(art["updated_at"]) if isinstance(art["updated_at"], str) else art["updated_at"],
                )
                for art in artifacts
            ]
        )

    except Exception as e:
        logger.exception(f"Error listing artifacts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{session_id}/{artifact_id}", response_model=ArtifactDetailResponse)
async def get_artifact(
    session_id: str,
    artifact_id: str,
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取 artifact 详情（包含当前版本内容）
    """
    result = await artifact_manager.read_artifact(
        session_id=session_id,
        artifact_id=artifact_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found in session '{session_id}'"
        )

    return ArtifactDetailResponse(
        id=result["id"],
        session_id=session_id,
        content_type=result["content_type"],
        title=result["title"],
        content=result["content"],
        current_version=result["version"],
        created_at=datetime.fromisoformat(result["created_at"]) if isinstance(result["created_at"], str) else result["created_at"],
        updated_at=datetime.fromisoformat(result["updated_at"]) if isinstance(result["updated_at"], str) else result["updated_at"],
    )


@router.get("/{session_id}/{artifact_id}/versions", response_model=VersionListResponse)
async def list_versions(
    session_id: str,
    artifact_id: str,
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取版本历史列表
    """
    repo = artifact_manager._ensure_repository()

    # 先检查 artifact 是否存在
    artifact = await repo.get_artifact(session_id, artifact_id)
    if not artifact:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found in session '{session_id}'"
        )

    versions = await repo.list_versions(session_id, artifact_id)

    return VersionListResponse(
        artifact_id=artifact_id,
        session_id=session_id,
        versions=[
            VersionSummary(
                version=v["version"],
                update_type=v["update_type"],
                created_at=datetime.fromisoformat(v["created_at"]) if isinstance(v["created_at"], str) else v["created_at"],
            )
            for v in versions
        ]
    )


@router.get("/{session_id}/{artifact_id}/versions/{version}", response_model=VersionDetailResponse)
async def get_version(
    session_id: str,
    artifact_id: str,
    version: int,
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取特定版本的完整内容
    """
    repo = artifact_manager._ensure_repository()

    # 获取版本
    ver = await repo.get_version(session_id, artifact_id, version)

    if ver is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of artifact '{artifact_id}' not found"
        )

    return VersionDetailResponse(
        version=ver.version,
        content=ver.content,
        update_type=ver.update_type,
        changes=ver.changes,
        created_at=ver.created_at,
    )
