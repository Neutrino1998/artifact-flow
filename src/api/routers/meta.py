"""
Meta Router

Exposes backend runtime constants the frontend needs (GET /api/v1/meta), so the
frontend reads them from a single source of truth instead of redefining values
that would drift from src/config.py. Values are static for the session — the
frontend fetches once and caches.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_client_config_manager, get_current_user
from api.services.auth import TokenPayload
from api.schemas.meta import ClientConfigResponse
from core.management.client_config_manager import ClientConfigInvariantError, ClientConfigManager
from utils.logger import get_logger

router = APIRouter()
logger = get_logger("ArtifactFlow")


@router.get("", response_model=ClientConfigResponse)
async def get_client_config(
    _current_user: TokenPayload = Depends(get_current_user),
    manager: ClientConfigManager = Depends(get_client_config_manager),
) -> ClientConfigResponse:
    """返回前端所需的后端常量（单一真相源）。值静态，前端取一次缓存即可。"""
    try:
        result = await manager.get()
    except ClientConfigInvariantError as e:
        logger.error(f"Client config unavailable: {e}")
        raise HTTPException(
            status_code=500,
            detail="Client configuration is unavailable",
        ) from e
    return ClientConfigResponse(**result)
