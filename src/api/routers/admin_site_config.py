"""Admin runtime site-config endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import require_admin
from api.schemas.site_config import (
    SiteNotificationsResponse,
    UpdateSiteNotificationsRequest,
)
from api.services.auth import TokenPayload
from core.site_config_manager import SiteConfigError, SiteConfigManager
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")

router = APIRouter()


def _manager_error_to_http(error: SiteConfigError) -> HTTPException:
    if error.status_code >= 500:
        logger.error(f"Site config operation failed: {error}")
    return HTTPException(status_code=error.status_code, detail=str(error))


@router.get("/site/notifications", response_model=SiteNotificationsResponse)
async def get_site_notifications(
    _admin: TokenPayload = Depends(require_admin),
) -> SiteNotificationsResponse:
    manager = SiteConfigManager()
    try:
        result = await manager.get_notifications()
    except SiteConfigError as e:
        raise _manager_error_to_http(e) from e
    return SiteNotificationsResponse(**result)


@router.put("/site/notifications", response_model=SiteNotificationsResponse)
async def update_site_notifications(
    request: UpdateSiteNotificationsRequest,
    _admin: TokenPayload = Depends(require_admin),
) -> SiteNotificationsResponse:
    manager = SiteConfigManager()
    try:
        result = await manager.update_notifications(
            request.notifications,
            expected_revision=request.expected_revision,
        )
    except SiteConfigError as e:
        raise _manager_error_to_http(e) from e
    return SiteNotificationsResponse(**result)
