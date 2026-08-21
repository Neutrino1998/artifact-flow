"""Authenticated user-facing site notifications."""

from fastapi import APIRouter, Depends

from api.dependencies import get_current_principal, get_site_config_manager
from api.schemas.site_config import SiteNotificationsResponse
from api.services.auth import AuthPrincipal
from core.management.site_config_manager import SiteConfigManager

router = APIRouter()


@router.get("", response_model=SiteNotificationsResponse)
async def get_notifications(
    _current_user: AuthPrincipal = Depends(get_current_principal),
    manager: SiteConfigManager = Depends(get_site_config_manager),
) -> SiteNotificationsResponse:
    result = await manager.get_notifications()
    return SiteNotificationsResponse(**result)
