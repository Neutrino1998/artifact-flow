import json

import pytest
from httpx import AsyncClient

from config import config

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

    async def test_duplicate_ids_rejected(self, admin_client: AsyncClient, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "SITE_CONFIG_DIR", str(tmp_path))

        resp = await admin_client.put(
            "/api/v1/admin/site/notifications",
            json={
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
