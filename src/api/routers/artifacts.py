"""
Artifacts Router

处理 Artifact 相关的 API 端点：
- GET /api/v1/artifacts/{session_id} - 列出 artifacts
- GET /api/v1/artifacts/{session_id}/{artifact_id} - 获取详情
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions - 版本列表
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} - 特定版本
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import config
from api.dependencies import get_artifact_manager, get_current_user, get_db_session
from api.services.auth import TokenPayload
from repositories.conversation_repo import ConversationRepository
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactDetailResponse,
    ArtifactSummary,
    VersionListResponse,
    VersionDetailResponse,
    VersionSummary,
    UploadResponse,
)
from tools.implementations.artifact_ops import ArtifactManager
from tools.utils.doc_converter import DocConverter
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


async def _verify_session_ownership(
    session_id: str, user: TokenPayload, session: AsyncSession
) -> None:
    """校验 session（= conversation）归属当前用户"""
    repo = ConversationRepository(session)
    conv = await repo.get_conversation(session_id)
    if not conv or conv.user_id != user.user_id:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.get("/{session_id}", response_model=ArtifactListResponse)
async def list_artifacts(
    session_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    列出 session 下所有 artifacts
    """
    await _verify_session_ownership(session_id, current_user, db_session)

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
                    source=art.get("source"),
                    created_at=datetime.fromisoformat(art["created_at"]) if isinstance(art["created_at"], str) else art["created_at"],
                    updated_at=datetime.fromisoformat(art["updated_at"]) if isinstance(art["updated_at"], str) else art["updated_at"],
                )
                for art in artifacts
            ]
        )

    except Exception as e:
        logger.exception(f"Error listing artifacts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    Upload a file and create an artifact from it.
    Supports text files, markdown, code, PDF, and Word documents.
    """
    await _verify_session_ownership(session_id, current_user, db_session)

    # Read file bytes
    file_bytes = await file.read()

    # Check size
    if len(file_bytes) > config.MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=422,
            detail=f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB "
                   f"(max {config.MAX_UPLOAD_SIZE / 1024 / 1024:.0f}MB)"
        )

    # Convert
    converter = DocConverter()
    try:
        result = await converter.convert(file_bytes, file.filename or "untitled")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Create artifact
    success, message, info = await artifact_manager.create_from_upload(
        session_id=session_id,
        filename=file.filename or "untitled",
        content=result.content,
        content_type=result.content_type,
        metadata=result.metadata,
    )

    if not success:
        raise HTTPException(status_code=500, detail=message)

    # Get created_at from DB
    memory = await artifact_manager.get_artifact(session_id, info["id"])

    return UploadResponse(
        id=info["id"],
        session_id=session_id,
        content_type=info["content_type"],
        title=info["title"],
        current_version=info["current_version"],
        source=info["source"],
        original_filename=info["original_filename"],
        created_at=memory.created_at if memory else datetime.now(),
    )


@router.get("/{session_id}/{artifact_id}/export")
async def export_artifact(
    session_id: str,
    artifact_id: str,
    format: str = Query(..., description="Export format (docx)"),
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    Export an artifact to a different format.
    Currently supports exporting text/markdown artifacts to docx.
    """
    await _verify_session_ownership(session_id, current_user, db_session)

    if format != "docx":
        raise HTTPException(status_code=422, detail=f"Unsupported export format: {format}")

    result = await artifact_manager.read_artifact(session_id, artifact_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found")

    if result["content_type"] != "text/markdown":
        raise HTTPException(
            status_code=422,
            detail=f"Only text/markdown artifacts can be exported to docx (got {result['content_type']})"
        )

    converter = DocConverter()
    try:
        docx_bytes = await converter.export_docx(result["content"])
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    filename = result["title"].replace("/", "-").replace("\\", "-") + ".docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/{artifact_id}", response_model=ArtifactDetailResponse)
async def get_artifact(
    session_id: str,
    artifact_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取 artifact 详情（包含当前版本内容）
    """
    await _verify_session_ownership(session_id, current_user, db_session)
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
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取版本历史列表
    """
    await _verify_session_ownership(session_id, current_user, db_session)
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
    current_user: TokenPayload = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
):
    """
    获取特定版本的完整内容
    """
    await _verify_session_ownership(session_id, current_user, db_session)
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
