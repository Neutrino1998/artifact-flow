"""Authenticated user-facing site notifications."""

from fastapi import APIRouter, Depends

from api.dependencies import get_current_user, get_site_config_manager
from api.schemas.site_config import SiteNotificationsResponse
from api.services.auth import TokenPayload
from core.site_config_manager import SiteConfigManager

router = APIRouter()


@router.get("", response_model=SiteNotificationsResponse)
async def get_notifications(
    _current_user: TokenPayload = Depends(get_current_user),
    manager: SiteConfigManager = Depends(get_site_config_manager),
) -> SiteNotificationsResponse:
    result = await manager.get_notifications()
    return SiteNotificationsResponse(**result)
