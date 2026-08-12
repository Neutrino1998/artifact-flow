"""Authenticated SSE observer endpoint."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from api.dependencies import get_current_user, get_stream_transport
from api.services.auth import TokenPayload
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
    current_user: TokenPayload = Depends(get_current_user),
    stream_transport: StreamTransport = Depends(get_stream_transport),
) -> StreamingResponse:
    return build_stream_response(
        stream_id=stream_id,
        stream_transport=stream_transport,
        user_id=current_user.user_id,
        last_event_id=request.headers.get("Last-Event-ID"),
    )
