"""DB-backed runtime notification configuration."""

from __future__ import annotations

from typing import Iterable

from api.schemas.site_config import SiteNotification
from repositories.site_notification_repo import SiteNotificationRepository


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
