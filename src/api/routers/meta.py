"""
Meta Router

Exposes backend runtime constants the frontend needs (GET /api/v1/meta), so the
frontend reads them from a single source of truth instead of redefining values
that would drift from src/config.py. Values are static for the session — the
frontend fetches once and caches.
"""

from fastapi import APIRouter, Depends

from config import config
from api.dependencies import get_agents, get_current_user
from api.services.auth import TokenPayload
from api.schemas.meta import ClientConfigResponse

router = APIRouter()


@router.get("", response_model=ClientConfigResponse)
async def get_client_config(
    current_user: TokenPayload = Depends(get_current_user),
) -> ClientConfigResponse:
    """返回前端所需的后端常量（单一真相源）。值静态，前端取一次缓存即可。"""
    agents = get_agents()
    # lead_agent is the user-facing coordinator and is guaranteed present
    # (engine + chat router both fail to start if it's missing). Direct key
    # access without a fallback so a misconfigured MD set fails loudly here too.
    lead_model = agents["lead_agent"].model
    return ClientConfigResponse(
        compaction_token_threshold=config.COMPACTION_TOKEN_THRESHOLD,
        lead_agent_model=lead_model,
    )
