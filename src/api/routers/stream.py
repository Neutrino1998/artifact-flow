"""Authenticated SSE observer endpoint."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from api.dependencies import get_stream_transport, require_scope
from api.services.auth import AuthPrincipal
from api.services.stream_response import SSE_OPENAPI_RESPONSES, build_stream_response
from api.services.stream_transport import StreamTransport

router = APIRouter()


@router.get(
    "/{stream_id}",
    response_class=StreamingResponse,
    responses=SSE_OPENAPI_RESPONSES,
)
async def stream_events(
    stream_id: str,
    request: Request,
    current_user: AuthPrincipal = Depends(require_scope("conversations:read")),
    stream_transport: StreamTransport = Depends(get_stream_transport),
) -> StreamingResponse:
    """Stream the complete same-user conversation feed.

    Artifact/tool content already copied into this feed remains conversation
    data; ``artifacts:read`` separately gates direct Artifact REST access.
    """
    return build_stream_response(
        stream_id=stream_id,
        stream_transport=stream_transport,
        user_id=current_user.user_id,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
