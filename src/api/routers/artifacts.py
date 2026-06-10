"""
Artifacts Router

处理 Artifact 相关的 API 端点：
- GET /api/v1/artifacts/{session_id} - 列出 artifacts
- GET /api/v1/artifacts/{session_id}/{artifact_id} - 获取详情（含版本列表和最新版本）
- GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version} - 特定版本
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response

from config import config
from api.dependencies import get_artifact_service, get_conversation_manager, get_current_user
from api.services.auth import TokenPayload
from core.conversation_manager import ConversationManager
from api.schemas.artifact import (
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactSummary,
    VersionDetailResponse,
    VersionSummary,
)
from tools.builtin.artifact_service import ArtifactService
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


@dataclass
class ConvertedUpload:
    """One uploaded file after size-check + conversion, before any DB write.

    Phase-1 output of POST /chat's two-phase attachment flow (convert-all →
    commit-all). Holds the converted text representation, plus — for blob-backed
    types (images / rich formats) — the raw bytes to persist into ArtifactBlob and
    the blob's true MIME. Pure-text uploads carry blob=None and free their bytes,
    so a batch of text files does not pin raw bytes in RAM; an image/docx batch
    necessarily retains its bytes (they must be stored anyway).
    """
    filename: str
    content: str
    content_type: str
    metadata: dict
    blob: bytes | None = None
    blob_content_type: str | None = None


async def convert_uploaded_file(file: UploadFile) -> ConvertedUpload:
    """Size-check + read + convert ONE uploaded file. Performs NO DB writes.

    Raises HTTPException on oversize (422), unsupported/invalid file (422), or
    conversion failure (500). Separated from the DB commit so POST /chat can
    validate+convert every attachment BEFORE creating the conversation or any
    artifact row: a bad file in the batch then aborts with zero DB state (no
    ghost conversation, no orphan artifacts) instead of leaving committed the
    files that happened to precede it in the loop. convert() is pure (bytes →
    text, no DB / session needed), which is what makes the early phase possible.
    """
    # Size-check BEFORE read so an oversize part is rejected without
    # materializing it in RAM. Starlette's multipart parser spools each part to
    # a temp file (rolls to disk past ~1MB) and sets UploadFile.size to the full
    # part length — so reading a 1GB part here would spike RAM even though
    # parsing kept it on disk. nginx caps the body (25MB) at the edge; this is
    # the in-app guard for anything that bypasses it.
    max_mb = config.MAX_UPLOAD_SIZE / 1024 / 1024
    if file.size is not None and file.size > config.MAX_UPLOAD_SIZE:
        detail = f"File too large: {file.size / 1024 / 1024:.1f}MB (max {max_mb:.0f}MB)"
        # 体积超限在进 converter 前就拒,也要落原因(同 ValueError 分支),否则
        # grep req-id 对超大文件仍只看到一条裸 422 access log。
        logger.warning(f"Upload rejected (422) for {file.filename!r}: {detail}")
        raise HTTPException(status_code=422, detail=detail)
    file_bytes = await file.read()
    # Fallback if the parser didn't populate .size (keeps the 422 contract;
    # the bytes are already in RAM by here, so the pre-check above is the real
    # memory guard).
    if len(file_bytes) > config.MAX_UPLOAD_SIZE:
        detail = f"File too large: {len(file_bytes) / 1024 / 1024:.1f}MB (max {max_mb:.0f}MB)"
        logger.warning(f"Upload rejected (422) for {file.filename!r}: {detail}")
        raise HTTPException(status_code=422, detail=detail)

    converter = DocConverter()
    try:
        result = await converter.convert(file_bytes, file.filename or "untitled")
    except ValueError as e:
        # 预期内的客户端错误(改后缀 / 超限 / 编码失败):用 WARNING 落原因,把
        # req-id ↔ 拒绝理由绑起来(否则 grep req-id 只看到一条 422 access log,
        # 看不出为什么)。不用 exception —— 无需堆栈,reason 字符串足够。
        logger.warning(f"Upload rejected (422) for {file.filename!r}: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.exception(f"File conversion failed for {file.filename!r}: {e}")
        error_detail = str(e) if config.DEBUG else "Internal server error"
        raise HTTPException(status_code=500, detail=error_detail)

    return ConvertedUpload(
        filename=file.filename or "untitled",
        content=result.content,
        content_type=result.content_type,
        metadata=result.metadata or {},
        blob=result.blob,
        blob_content_type=result.blob_content_type,
    )


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
                    has_blob=bool(art.get("blob_content_type")),
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
    responses={
        200: {
            "content": {
                # The handler returns the blob's TRUE content_type — image/png,
                # image/jpeg, application/pdf, the docx OOXML MIME, the octet-
                # stream fallback, and (C-phase) arbitrary sandbox-written types.
                # `*/*` covers any media type without enumerating a drift-prone
                # list; schema type=string/format=binary types the body as binary
                # (string/Blob) in generated clients, not `unknown`.
                "*/*": {"schema": {"type": "string", "format": "binary"}},
            },
            "description": "Raw artifact blob (image inline, else attachment).",
        }
    },
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

    Images are served `inline` so a frontend `<img src=.../raw>` renders in place;
    everything else `attachment` (download). Content-Type is the blob's true MIME
    (from the Service, which prefers metadata.blob_content_type over the artifact's
    possibly-converted content_type).
    """
    await _verify_session_ownership(session_id, current_user, conversation_manager)

    blob = await artifact_service.get_blob(session_id, artifact_id)
    if blob is None:
        raise HTTPException(status_code=404, detail=f"Artifact blob '{artifact_id}' not found")

    content_type = blob["content_type"] or "application/octet-stream"
    disposition = "inline" if content_type.startswith("image/") else "attachment"

    filename = blob["filename"].replace("/", "-").replace("\\", "-")
    # RFC 5987: filename* for non-ASCII, with sanitized ASCII fallback.
    from urllib.parse import quote
    import re as _re
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    ascii_fallback = _re.sub(r'["\x00-\x1f\x7f]', "_", ascii_fallback)
    utf8_encoded = quote(filename, safe="")
    return Response(
        content=blob["data"],
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f'{disposition}; filename="{ascii_fallback}"; '
                f"filename*=UTF-8''{utf8_encoded}"
            )
        },
    )


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
        has_blob=bool(result.get("blob_content_type")),
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
