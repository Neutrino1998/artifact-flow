"""Admin-only transport contract for instance diagnostic events."""

from __future__ import annotations

from httpx import AsyncClient

from config import config


async def test_instance_events_endpoint_is_admin_only_and_validates_id(
    monkeypatch,
    tmp_path,
    admin_client: AsyncClient,
    client: AsyncClient,
):
    instance_id = "backend-api"
    log_root = tmp_path / "logs"
    obs_root = tmp_path / "observability"
    monkeypatch.setenv("ARTIFACTFLOW_LOG_DIR", str(log_root))
    monkeypatch.setattr(config, "OBS_LOOP_LAG_LOG_PATH", str(obs_root / "loop-lag.jsonl"))
    monkeypatch.setattr(config, "OBS_METRICS_LOG_PATH", str(obs_root / "metrics.jsonl"))
    monkeypatch.setattr(config, "OBS_AUTOHEAL_MARKER_PATH", "")

    error_log = log_root / instance_id / "artifactflow_error.log"
    error_log.parent.mkdir(parents=True)
    error_log.write_text(
        "2026-07-30 10:00:00,000 - ArtifactFlow - ERROR - "
        "[backend-api|req-1|no-ctx|no-ctx] api.py:f:1 - boom\n",
        encoding="utf-8",
    )

    forbidden = await client.get(f"/api/v1/admin/instances/{instance_id}/events")
    assert forbidden.status_code == 403

    response = await admin_client.get(
        f"/api/v1/admin/instances/{instance_id}/events?kind=error&limit=10"
    )
    assert response.status_code == 200
    assert response.json()["events"][0]["summary"] == "boom"
    assert response.json()["events"][0]["type"] == "error"

    invalid_kind = await admin_client.get(
        f"/api/v1/admin/instances/{instance_id}/events?kind=autoheal"
    )
    assert invalid_kind.status_code == 422

    invalid = await admin_client.get("/api/v1/admin/instances/bad$id/events")
    assert invalid.status_code == 400
