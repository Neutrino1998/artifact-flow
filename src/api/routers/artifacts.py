"""
Artifacts Router

处理 Artifact 相关的 API 端点：
- GET /api/v1/artifacts/{session_id} - 列出 artifacts
- GET /api/v1/artifacts/{session_id}/{artifact_id} - 获取详情（含版本列表和最新版本）
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} - 特定版本
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response

from config import config
from api.dependencies import get_artifact_manager, get_conversation_manager, get_current_user
from api.services.auth import TokenPayload
from core.conversation_manager import ConversationManager
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
    VersionDetailResponse,
    VersionSummary,
    UploadResponse,
)
from tools.builtin.artifact_ops import ArtifactManager
from utils.doc_converter import DocConverter
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


async def _verify_session_ownership(
    session_id: str, user: TokenPayload, conversation_manager: ConversationManager
) -> None:
    """校验 session（= conversation）归属当前用户"""
    if not await conversation_manager.verify_ownership(session_id, user.user_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.post("/upload", response_model=UploadResponse)
async def upload_file_new_session(
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    Upload a file and create a new conversation for it.
    Use this when no session/conversation exists yet.
    """
    from uuid import uuid4

    # Read and validate file
    file_bytes = await file.read()
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
        error_detail = str(e) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

    # Create conversation (auto-creates ArtifactSession)
    conversation_id = f"conv-{uuid4().hex}"
    await conversation_manager.ensure_conversation_exists(
        conversation_id, user_id=current_user.user_id
    )

    # Create artifact
    success, message, info = await artifact_manager.create_from_upload(
        session_id=conversation_id,
        filename=file.filename or "untitled",
        content=result.content,
        content_type=result.content_type,
        metadata=result.metadata,
    )

    if not success:
        error_detail = message if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

    memory = await artifact_manager.get_artifact(conversation_id, info["id"])

    return UploadResponse(
        id=info["id"],
        session_id=conversation_id,
        content_type=info["content_type"],
        title=info["title"],
        current_version=info["current_version"],
        source=info["source"],
        original_filename=info["original_filename"],
        created_at=memory.created_at if memory else datetime.now(),
    )


@router.get("/{session_id}", response_model=ArtifactListResponse)
async def list_artifacts(
    session_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    列出 session 下所有 artifacts
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    try:
        # DB query via request-scoped manager (own session)
        artifacts = await artifact_manager.list_artifacts(
            session_id=session_id,
            include_content=False
        )

        # Overlay / append in-memory artifacts from active engine execution.
        # Covers both new (not yet in DB) and dirty (updated since last flush).
        # Only reads the cache dict — no DB calls on the controller's session.
        active = ArtifactManager.get_active(session_id)
        if active:
            cache = active.get_cached_artifacts(session_id)
            if cache:
                db_index = {art["id"]: i for i, art in enumerate(artifacts)}
                for aid, memory in cache.items():
                    entry = {
                        "id": memory.id,
                        "content_type": memory.content_type,
                        "title": memory.title,
                        "version": memory.current_version,
                        "source": memory.source,
                        "original_filename": (memory.metadata or {}).get("original_filename"),
                        "created_at": memory.created_at.isoformat(),
                        "updated_at": memory.updated_at.isoformat(),
                    }
                    if aid in db_index:
                        artifacts[db_index[aid]] = entry  # overlay dirty
                    else:
                        artifacts.append(entry)  # new

        return ArtifactListResponse(
            session_id=session_id,
            artifacts=[
                ArtifactSummary(
                    id=art["id"],
                    content_type=art["content_type"],
                    title=art["title"],
                    current_version=art["version"],
                    source=art.get("source"),
                    original_filename=art.get("original_filename"),
                    created_at=datetime.fromisoformat(art["created_at"]),
                    updated_at=datetime.fromisoformat(art["updated_at"]),
                )
                for art in artifacts
            ]
        )

    except Exception as e:
        logger.exception(f"Error listing artifacts: {e}")
        error_detail = str(e) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_file(
    session_id: str,
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    Upload a file and create an artifact from it.
    Supports text files, markdown, code, PDF, and Word documents.
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

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
        error_detail = str(e) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

    # Create artifact
    success, message, info = await artifact_manager.create_from_upload(
        session_id=session_id,
        filename=file.filename or "untitled",
        content=result.content,
        content_type=result.content_type,
        metadata=result.metadata,
    )

    if not success:
        error_detail = message if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

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
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    Export an artifact to a different format.
    Currently supports exporting text/markdown artifacts to docx.

    Note: reads from DB only — during execution, exports the last flushed
    version, not in-memory edits.  Frontend hides export while streaming.
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

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
        error_detail = str(e) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

    filename = result["title"].replace("/", "-").replace("\\", "-") + ".docx"
    # RFC 5987: use filename* for non-ASCII names, with ASCII fallback
    from urllib.parse import quote
    import re as _re
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    # Sanitize quotes and control characters for safe Content-Disposition
    ascii_fallback = _re.sub(r'["\x00-\x1f\x7f]', "_", ascii_fallback)
    utf8_encoded = quote(filename, safe="")
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{utf8_encoded}"
            )
        },
    )


@router.get("/{session_id}/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    session_id: str,
    artifact_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    获取 artifact 当前内容和版本列表
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    # Try DB first via request-scoped manager
    result = await artifact_manager.read_artifact(
        session_id=session_id,
        artifact_id=artifact_id
    )

    # Overlay or supply from active engine's in-memory cache (cache-only, no DB).
    # Handles both new artifacts (DB miss) and dirty ones (DB hit but stale).
    active = ArtifactManager.get_active(session_id)
    if active:
        memory = active.get_cached_artifacts(session_id).get(artifact_id)
        if memory:
            result = {
                "id": memory.id,
                "content_type": memory.content_type,
                "title": memory.title,
                "content": memory.content,
                "version": memory.current_version,
                "source": memory.source,
                "original_filename": (memory.metadata or {}).get("original_filename"),
                "created_at": memory.created_at.isoformat(),
                "updated_at": memory.updated_at.isoformat(),
            }

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found in session '{session_id}'"
        )

    # Fetch persisted version list from DB.
    # During execution, current_version (from cache) may be ahead of this list.
    # This is intentional — frontend hides the version selector while streaming.
    versions = await artifact_manager.list_versions(session_id, artifact_id)
    version_summaries = [
        VersionSummary(
            version=v.version,
            update_type=v.update_type,
            created_at=v.created_at,
        )
        for v in versions
    ]

    current_ver = result["version"]

    return ArtifactResponse(
        id=result["id"],
        session_id=session_id,
        content_type=result["content_type"],
        title=result["title"],
        content=result["content"],
        current_version=current_ver,
        source=result.get("source"),
        original_filename=result.get("original_filename"),
        created_at=datetime.fromisoformat(result["created_at"]),
        updated_at=datetime.fromisoformat(result["updated_at"]),
        versions=version_summaries,
    )


@router.get("/{session_id}/{artifact_id}/versions/{version}", response_model=VersionDetailResponse)
async def get_version(
    session_id: str,
    artifact_id: str,
    version: int,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    获取特定版本的完整内容

    Note: DB-only — unflushed in-memory versions return 404.
    Frontend hides version selector while streaming, so this is unreachable
    for versions that only exist in cache.
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    ver = await artifact_manager.get_version(session_id, artifact_id, version)

    if ver is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of artifact '{artifact_id}' not found"
        )

    return VersionDetailResponse(
        version=ver.version,
        content=ver.content,
        update_type=ver.update_type,
        created_at=ver.created_at,
    )
