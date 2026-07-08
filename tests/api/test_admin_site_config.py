import asyncio
from datetime import datetime
import json
import time

import pytest
from httpx import AsyncClient

from config import config
from api.schemas.site_config import SiteNotification
from core.site_config_manager import SiteConfigConflictError, SiteConfigManager

pytestmark = pytest.mark.asyncio


class TestSiteNotificationsAuth:
    async def test_anon_blocked(self, anon_client: AsyncClient):
        resp = await anon_client.get("/api/v1/admin/site/notifications")
        assert resp.status_code == 401

    async def test_regular_user_blocked(self, client: AsyncClient):
        resp = await client.get("/api/v1/admin/site/notifications")
        assert resp.status_code == 403


class TestSiteNotificationsCrud:
    async def test_missing_file_returns_empty(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.get("/api/v1/admin/site/notifications")

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"notifications": [], "revision": "missing"}

    async def test_put_writes_notifications_json(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        body = {
            "expected_revision": "missing",
            "notifications": [
                {
                    "id": "maintenance-2026-05-20",
                    "severity": "warn",
                    "title": "系统维护通知",
                    "body": "## 维护时间\n今晚",
                    "starts_at": "2026-05-15T00:00:00Z",
                    "ends_at": "2026-05-20T04:00:00Z",
                    "dismissible": False,
                }
            ],
        }

        resp = await admin_client.put("/api/v1/admin/site/notifications", json=body)

        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["revision"] != "missing"
        assert payload["notifications"][0]["id"] == "maintenance-2026-05-20"

        on_disk = json.loads((tmp_path / "notifications.json").read_text())
        assert on_disk == payload["notifications"]

    async def test_revision_conflict_rejects_stale_update(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "notifications.json").write_text("[]\n")

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={"expected_revision": "missing", "notifications": []},
        )

        assert resp.status_code == 409
        assert json.loads((tmp_path / "notifications.json").read_text()) == []

    async def test_put_requires_revision_and_notifications(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        assert (await admin_client.put("/api/v1/admin/site/notifications", json={})).status_code == 422
        assert (
            await admin_client.put(
                "/api/v1/admin/site/notifications",
                json={"notifications": []},
            )
        ).status_code == 422
        assert (
            await admin_client.put(
                "/api/v1/admin/site/notifications",
                json={"expected_revision": "missing"},
            )
        ).status_code == 422

    async def test_blank_required_text_is_rejected_before_write(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": "missing",
                "notifications": [
                    {"id": "   ", "severity": "info", "title": "   ", "body": "   "}
                ],
            },
        )

        assert resp.status_code == 422
        assert not (tmp_path / "notifications.json").exists()

    async def test_naive_datetime_is_saved_with_server_timezone(
        self,
        admin_client: AsyncClient,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": "missing",
                "notifications": [{
                    "id": "n1",
                    "severity": "info",
                    "title": "T",
                    "body": "B",
                    "starts_at": "2026-05-15 00:00",
                    "ends_at": "2026-05-20T04:00:00Z",
                }],
            },
        )

        assert resp.status_code == 200, resp.text
        saved = resp.json()["notifications"][0]
        assert saved["starts_at"] != "2026-05-15 00:00"
        assert datetime.fromisoformat(saved["starts_at"]).tzinfo is not None
        assert datetime.fromisoformat(saved["ends_at"]).tzinfo is not None
        assert json.loads((tmp_path / "notifications.json").read_text())[0] == saved

    async def test_mixed_datetime_window_error_is_422(
        self,
        admin_client: AsyncClient,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": "missing",
                "notifications": [{
                    "id": "n1",
                    "severity": "info",
                    "title": "T",
                    "body": "B",
                    "starts_at": "2026-05-20 00:00",
                    "ends_at": "2026-05-19T00:00:00Z",
                }],
            },
        )

        assert resp.status_code == 422

    async def test_concurrent_same_revision_allows_one_writer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))
        manager = SiteConfigManager(str(tmp_path))
        original_write = SiteConfigManager._atomic_write

        def slow_write(self, raw):
            time.sleep(0.1)
            return original_write(self, raw)

        monkeypatch.setattr(SiteConfigManager, "_atomic_write", slow_write)
        n1 = SiteNotification(id="n1", severity="info", title="A", body="A")
        n2 = SiteNotification(id="n2", severity="warn", title="B", body="B")

        results = await asyncio.gather(
            manager.update_notifications([n1], expected_revision="missing"),
            manager.update_notifications([n2], expected_revision="missing"),
            return_exceptions=True,
        )

        assert sum(isinstance(r, SiteConfigConflictError) for r in results) == 1
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) == 1
        on_disk = json.loads((tmp_path / "notifications.json").read_text())
        assert [n["id"] for n in on_disk] == [successes[0]["notifications"][0]["id"]]

    async def test_duplicate_ids_rejected(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
                "expected_revision": "missing",
                "notifications": [
                    {"id": "same", "severity": "info", "title": "A", "body": "A"},
                    {"id": "same", "severity": "warn", "title": "B", "body": "B"},
                ],
            },
        )

        assert resp.status_code == 422

    async def test_bad_existing_json_is_logged_and_500(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "notifications.json").write_text("{bad")

        resp = await admin_client.get("/api/v1/admin/site/notifications")

        assert resp.status_code == 500
        assert "not valid JSON" in resp.json()["detail"]
