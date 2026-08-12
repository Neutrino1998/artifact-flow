"""
Artifacts Router

处理 Artifact 相关的 API 端点：
- GET /api/v1/artifacts/{session_id} - 列出 artifacts
- GET /api/v1/artifacts/{session_id}/{artifact_id} - 获取详情（含版本列表和最新版本）
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} - 特定版本
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from config import config
from api.dependencies import get_artifact_service, get_conversation_manager, get_current_user
from api.artifact_raw_response import RAW_ARTIFACT_RESPONSES, build_artifact_blob_response
from api.services.auth import TokenPayload
from core.management.conversation_manager import ConversationManager
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
    VersionDetailResponse,
    VersionSummary,
)
from tools.builtin.artifact_service import ArtifactService
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


async def _verify_session_ownership(
    session_id: str, user: TokenPayload, conversation_manager: ConversationManager
) -> None:
    """校验 session（= conversation）归属当前用户"""
    if not await conversation_manager.verify_ownership(session_id, user.user_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")


@router.get("/{session_id}", response_model=ArtifactListResponse)
async def list_artifacts(
    session_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_service: ArtifactService = Depends(get_artifact_service),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    列出 session 下所有 artifacts
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    try:
        # 纯 DB 读(请求级 Service,自带空 WorkingSet)。删 _active_managers overlay 后,
        # turn 中的 live 态由前端订阅 ARTIFACT_* 事件流 reduce,不再靠 REST 轮询
        # 跨进程读执行 worker 的内存——后者在多 worker 下静默失效(见重构 plan 决策 1)。
        artifacts = await artifact_service.list_artifacts(
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
                    original_filename=art.get("original_filename"),
                    has_blob=bool(art.get("has_blob")),
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


@router.get(
    "/{session_id}/{artifact_id}/raw",
    # The handler returns a binary `Response` (the blob bytes), NOT JSON.
    # response_class=Response drops FastAPI's default application/json 200 media
    # type; `responses` then declares the real binary content types so the
    # generated OpenAPI / TS client advertises the correct contract.
    response_class=Response,
    responses=RAW_ARTIFACT_RESPONSES,
)
async def get_artifact_raw(
    session_id: str,
    artifact_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_service: ArtifactService = Depends(get_artifact_service),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Serve an artifact's raw binary blob (uploaded image / rich-format source).

    DB-only read (request-scoped Service, empty WorkingSet) — like all GETs here,
    during execution it serves the last flushed blob. 404 when the artifact has no
    blob (pure-text artifacts) or doesn't exist; not logged (self-evident 404).

    Safe raster images are served `inline` so a frontend `<img src=.../raw>`
    renders in place; everything else is `attachment` (download). Do not inline
    every `image/*`: SVG is XML and must not be treated as a safe image blob.
    Content-Type is the artifact's `content_type` — under the XOR model a blob
    artifact's content_type is the original file's true MIME.
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    blob = await artifact_service.get_blob(session_id, artifact_id)
    if blob is None:
        raise HTTPException(status_code=404, detail=f"Artifact blob '{artifact_id}' not found")

    return build_artifact_blob_response(blob)


@router.get("/{session_id}/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    session_id: str,
    artifact_id: str,
    current_user: TokenPayload = Depends(get_current_user),
    artifact_service: ArtifactService = Depends(get_artifact_service),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    获取 artifact 当前内容和版本列表
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    # 纯 DB 读(无 overlay)。turn 中的 live 内容由前端事件流 reduce;此端点返回
    # 已 flush 的 DB 权威态,turn 中故意落后于 live(见重构 plan 决策 6)。
    result = await artifact_service.read_artifact(
        session_id=session_id,
        artifact_id=artifact_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found in session '{session_id}'"
        )

    # Fetch persisted version list from DB.
    # During execution, current_version (from cache) may be ahead of this list.
    # This is intentional — frontend hides the version selector while streaming.
    versions = await artifact_service.list_versions(session_id, artifact_id)
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
        has_blob=bool(result.get("has_blob")),
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
    artifact_service: ArtifactService = Depends(get_artifact_service),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """
    获取特定版本的完整内容

    Note: DB-only — unflushed in-memory versions return 404.
    Frontend hides version selector while streaming, so this is unreachable
    for versions that only exist in cache.
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    ver = await artifact_service.get_version(session_id, artifact_id, version)

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
