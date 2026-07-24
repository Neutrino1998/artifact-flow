"""DB-backed global notification configuration API tests."""

import asyncio
from datetime import datetime
import os
import time

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import SiteNotificationConfig

pytestmark = pytest.mark.asyncio


class TestSiteNotificationsAuth:
    async def test_admin_endpoints_require_admin(
        self,
        anon_client: AsyncClient,
        client: AsyncClient,
    ):
        assert (
            await anon_client.get("/api/v1/admin/site/notifications")
        ).status_code == 401
        assert (
            await client.get("/api/v1/admin/site/notifications")
        ).status_code == 403

    async def test_user_endpoint_requires_login(self, anon_client: AsyncClient):
        assert (await anon_client.get("/api/v1/notifications")).status_code == 401


class TestSiteNotificationsCrud:
    async def test_missing_dev_row_returns_empty_config(
        self,
        admin_client: AsyncClient,
    ):
        # SQLite create_all does not run Alembic's singleton seed; the API keeps
        # revision=0 as the explicit empty/dev bootstrap state.
        response = await admin_client.get("/api/v1/admin/site/notifications")
        assert response.status_code == 200
        assert response.json() == {"notifications": [], "revision": 0}

    async def test_admin_put_is_visible_through_user_api(
        self,
        admin_client: AsyncClient,
        client: AsyncClient,
    ):
        body = {
            "expected_revision": 0,
            "notifications": [
                {
                    "id": "maintenance-2026-07-24",
                    "severity": "warn",
                    "title": "系统维护通知",
                    "body": "## 维护时间\n今晚",
                    "starts_at": "2026-07-24T00:00:00Z",
                    "ends_at": "2026-07-25T04:00:00Z",
                    "dismissible": False,
                }
            ],
        }

        saved = await admin_client.put(
            "/api/v1/admin/site/notifications", json=body
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["revision"] == 1

        user_response = await client.get("/api/v1/notifications")
        assert user_response.status_code == 200
        assert user_response.json() == saved.json()

    async def test_revision_conflict_rejects_stale_update(
        self,
        admin_client: AsyncClient,
    ):
        first = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={"expected_revision": 0, "notifications": []},
        )
        assert first.status_code == 200

        stale = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": 0,
                "notifications": [
                    {"id": "stale", "severity": "info", "title": "A", "body": "B"}
                ],
            },
        )
        assert stale.status_code == 409

        current = await admin_client.get("/api/v1/admin/site/notifications")
        assert current.json() == first.json()

    async def test_concurrent_same_revision_allows_one_writer(
        self,
        admin_client: AsyncClient,
    ):
        initial = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={"expected_revision": 0, "notifications": []},
        )
        assert initial.status_code == 200

        responses = await asyncio.gather(
            admin_client.put(
                "/api/v1/admin/site/notifications",
                json={
                    "expected_revision": 1,
                    "notifications": [
                        {"id": "n1", "severity": "info", "title": "A", "body": "A"}
                    ],
                },
            ),
            admin_client.put(
                "/api/v1/admin/site/notifications",
                json={
                    "expected_revision": 1,
                    "notifications": [
                        {"id": "n2", "severity": "warn", "title": "B", "body": "B"}
                    ],
                },
            ),
        )

        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = next(response for response in responses if response.status_code == 200)
        current = await admin_client.get("/api/v1/admin/site/notifications")
        assert current.json() == winner.json()

    async def test_put_requires_non_negative_revision_and_notifications(
        self,
        admin_client: AsyncClient,
    ):
        assert (
            await admin_client.put("/api/v1/admin/site/notifications", json={})
        ).status_code == 422
        assert (
            await admin_client.put(
                "/api/v1/admin/site/notifications",
                json={"notifications": []},
            )
        ).status_code == 422
        assert (
            await admin_client.put(
                "/api/v1/admin/site/notifications",
                json={"expected_revision": -1, "notifications": []},
            )
        ).status_code == 422

    async def test_blank_required_text_and_duplicate_ids_are_rejected(
        self,
        admin_client: AsyncClient,
    ):
        blank = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": 0,
                "notifications": [
                    {"id": "   ", "severity": "info", "title": "   ", "body": "   "}
                ],
            },
        )
        assert blank.status_code == 422

        duplicate = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": 0,
                "notifications": [
                    {"id": "same", "severity": "info", "title": "A", "body": "A"},
                    {"id": "same", "severity": "warn", "title": "B", "body": "B"},
                ],
            },
        )
        assert duplicate.status_code == 422

    async def test_naive_datetime_is_normalized_with_server_timezone(
        self,
        admin_client: AsyncClient,
    ):
        response = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": 0,
                "notifications": [
                    {
                        "id": "n1",
                        "severity": "info",
                        "title": "T",
                        "body": "B",
                        "starts_at": "2026-07-24 00:00",
                    }
                ],
            },
        )
        assert response.status_code == 200
        starts_at = response.json()["notifications"][0]["starts_at"]
        assert starts_at != "2026-07-24 00:00"
        assert datetime.fromisoformat(starts_at).tzinfo is not None

    async def test_naive_datetime_uses_target_date_dst_offset(
        self,
        admin_client: AsyncClient,
    ):
        if not hasattr(time, "tzset"):
            pytest.skip("time.tzset is unavailable on this platform")

        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            response = await admin_client.put(
                "/api/v1/admin/site/notifications",
                json={
                    "expected_revision": 0,
                    "notifications": [
                        {
                            "id": "winter",
                            "severity": "info",
                            "title": "Winter",
                            "body": "B",
                            "starts_at": "2026-01-15 12:00",
                        },
                        {
                            "id": "summer",
                            "severity": "info",
                            "title": "Summer",
                            "body": "B",
                            "starts_at": "2026-07-15 12:00",
                        },
                    ],
                },
            )
            assert response.status_code == 200
            items = response.json()["notifications"]
            assert items[0]["starts_at"].endswith("-05:00")
            assert items[1]["starts_at"].endswith("-04:00")
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()

    async def test_malformed_persisted_payload_is_logged_server_error(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        db_session.add(
            SiteNotificationConfig(
                id=1,
                notifications={"not": "an array"},
                revision=7,
            )
        )
        await db_session.commit()

        response = await client.get("/api/v1/notifications")
        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"
