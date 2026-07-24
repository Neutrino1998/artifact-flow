"""Admin runtime notification-config endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_site_config_manager, require_admin
from api.schemas.site_config import (
    SiteNotificationsResponse,
    UpdateSiteNotificationsRequest,
)
from api.services.auth import TokenPayload
from core.site_config_manager import SiteConfigConflictError, SiteConfigManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


@router.get("/site/notifications", response_model=SiteNotificationsResponse)
async def get_site_notifications(
    _admin: TokenPayload = Depends(require_admin),
    manager: SiteConfigManager = Depends(get_site_config_manager),
) -> SiteNotificationsResponse:
    result = await manager.get_notifications()
    return SiteNotificationsResponse(**result)


@router.put("/site/notifications", response_model=SiteNotificationsResponse)
async def update_site_notifications(
    request: UpdateSiteNotificationsRequest,
    _admin: TokenPayload = Depends(require_admin),
    manager: SiteConfigManager = Depends(get_site_config_manager),
) -> SiteNotificationsResponse:
    try:
        result = await manager.update_notifications(
            request.notifications,
            expected_revision=request.expected_revision,
        )
    except SiteConfigConflictError as e:
        # A stale bulk edit is an expected admin-caused business rejection, but
        # it is non-obvious enough that ops need its reason beside request_id.
        logger.warning(f"Notification config update rejected (409): {e}")
        raise HTTPException(status_code=409, detail=str(e)) from e
    return SiteNotificationsResponse(**result)
