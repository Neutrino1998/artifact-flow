"""Runtime site-config schemas.

These models cover small operator-managed JSON files under config/site. They
are not database entities; the admin API validates and writes the same files
that the frontend already serves from /site/*.json.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _parse_iso_datetime(value: str) -> datetime:
    """Accept ISO8601 strings including trailing-Z and local naive forms."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            raise ValueError("server local timezone is unavailable")
        dt = dt.replace(tzinfo=local_tz)
    return dt


def _normalize_iso_datetime(value: str) -> str:
    return _parse_iso_datetime(value).isoformat(timespec="seconds")


class SiteNotification(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    severity: Literal["info", "warn", "critical"]
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=20000)
    starts_at: Optional[str] = Field(default=None, max_length=64)
    ends_at: Optional[str] = Field(default=None, max_length=64)
    dismissible: bool = True

    @field_validator("id", "title", "body", "starts_at", "ends_at", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_iso_datetime(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        try:
            return _normalize_iso_datetime(value)
        except ValueError as e:
            raise ValueError(
                "must be an ISO8601 datetime; timezone is optional and defaults "
                "to the server local timezone"
            ) from e

    @model_validator(mode="after")
    def validate_time_window(self) -> "SiteNotification":
        if self.starts_at and self.ends_at:
            if _parse_iso_datetime(self.starts_at) > _parse_iso_datetime(self.ends_at):
                raise ValueError("starts_at must be before ends_at")
        return self


class SiteNotificationsResponse(BaseModel):
    notifications: List[SiteNotification]
    revision: str


class UpdateSiteNotificationsRequest(BaseModel):
    notifications: List[SiteNotification] = Field(..., max_length=50)
    expected_revision: str = Field(..., min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "UpdateSiteNotificationsRequest":
        seen: set[str] = set()
        for item in self.notifications:
            if item.id in seen:
                raise ValueError(f"duplicate notification id: {item.id}")
            seen.add(item.id)
        return self
