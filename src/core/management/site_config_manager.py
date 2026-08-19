"""DB-backed runtime notification configuration management."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional

from repositories.site_notification_repo import SiteNotificationRepository


def _parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.astimezone()
    return dt


def _normalize_iso_datetime(value: str) -> str:
    return _parse_iso_datetime(value).isoformat(timespec="seconds")


class SiteNotification(BaseModel):
    """Validated notification value shared by API schemas and persisted config."""

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
        return value.strip() if isinstance(value, str) else value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def validate_iso_datetime(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        try:
            return _normalize_iso_datetime(value)
        except ValueError as exc:
            raise ValueError(
                "must be an ISO8601 datetime; timezone is optional and defaults "
                "to the server local timezone"
            ) from exc

    @model_validator(mode="after")
    def validate_time_window(self) -> "SiteNotification":
        if self.starts_at and self.ends_at:
            if _parse_iso_datetime(self.starts_at) > _parse_iso_datetime(self.ends_at):
                raise ValueError("starts_at must be before ends_at")
        return self


class SiteConfigConflictError(Exception):
    """The caller edited a stale notification revision."""


class SiteConfigInvalidError(Exception):
    """The persisted JSON row bypassed normal schema validation."""


class SiteConfigManager:
    def __init__(self, repository: SiteNotificationRepository):
        self._repository = repository

    async def get_notifications(self) -> dict:
        row = await self._repository.get()
        if row is None:
            return {"notifications": [], "revision": 0}

        # DB JSON can be changed outside the API. Revalidate at the manager
        # boundary so malformed operator data fails loudly instead of reaching
        # every browser as a subtly partial notification list.
        notifications = self._validate_payload(row.notifications)
        return {"notifications": notifications, "revision": row.revision}

    async def update_notifications(
        self,
        notifications: Iterable[SiteNotification],
        *,
        expected_revision: int,
    ) -> dict:
        payload = [
            notification.model_dump(exclude_none=True)
            for notification in notifications
        ]
        row = await self._repository.compare_and_swap(
            payload,
            expected_revision=expected_revision,
        )
        if row is None:
            raise SiteConfigConflictError(
                "Notifications changed since they were loaded; refresh and retry"
            )
        return {
            "notifications": self._validate_payload(row.notifications),
            "revision": row.revision,
        }

    @staticmethod
    def _validate_payload(payload: object) -> list[dict]:
        try:
            if not isinstance(payload, list):
                raise ValueError("payload must be a JSON array")
            if len(payload) > 50:
                raise ValueError("payload exceeds the 50-notification limit")

            notifications: list[dict] = []
            seen: set[str] = set()
            for item in payload:
                notification = SiteNotification.model_validate(item)
                if notification.id in seen:
                    raise ValueError(f"duplicate notification id: {notification.id}")
                seen.add(notification.id)
                notifications.append(notification.model_dump(exclude_none=True))
            return notifications
        except Exception as e:
            # Use a dedicated server-state exception rather than ValueError:
            # the app's global ValueError handler is intentionally a 400 mapper,
            # but malformed persisted JSON is a server-side 500.
            raise SiteConfigInvalidError(
                f"Stored notification config is invalid: {e}"
            ) from e
