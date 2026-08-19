"""Runtime notification configuration schemas."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, model_validator

from core.management.site_config_manager import SiteNotification


class SiteNotificationsResponse(BaseModel):
    notifications: List[SiteNotification]
    revision: int = Field(..., ge=0)


class UpdateSiteNotificationsRequest(BaseModel):
    notifications: List[SiteNotification] = Field(..., max_length=50)
    expected_revision: int = Field(..., ge=0)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "UpdateSiteNotificationsRequest":
        seen: set[str] = set()
        for item in self.notifications:
            if item.id in seen:
                raise ValueError(f"duplicate notification id: {item.id}")
            seen.add(item.id)
        return self
