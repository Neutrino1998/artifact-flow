"""Admin instance-event history reads the existing bounded log substrates."""

from __future__ import annotations

import json
from pathlib import Path

from config import config
from observability.instance_events import read_instance_events


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _configure_paths(monkeypatch, tmp_path: Path, instance_id: str) -> tuple[Path, Path, Path]:
    log_root = tmp_path / "logs"
    obs_root = tmp_path / "observability"
    monkeypatch.setenv("ARTIFACTFLOW_LOG_DIR", str(log_root))
    monkeypatch.setattr(config, "OBS_LOOP_LAG_LOG_PATH", str(obs_root / "loop-lag.jsonl"))
    monkeypatch.setattr(config, "OBS_METRICS_LOG_PATH", str(obs_root / "metrics.jsonl"))
    return (
        log_root / instance_id / "artifactflow_error.log",
        obs_root / instance_id / "loop-lag.jsonl",
        obs_root / instance_id / "metrics.jsonl",
    )


def test_reads_error_wedge_and_metrics(monkeypatch, tmp_path):
    instance_id = "backend-1"
    error_log, loop_log, metrics_log = _configure_paths(
        monkeypatch, tmp_path, instance_id
    )

    error_log.parent.mkdir(parents=True)
    error_log.write_text(
        "2026-07-30 15:07:00,000 - ArtifactFlow - ERROR - "
        "[backend-1|req-1|conv-1|msg-1] engine.py:run:42 - LLM call failed\n"
        "Traceback (most recent call last):\n"
        "  File \"engine.py\", line 42, in run\n"
        "RuntimeError: boom\n",
        encoding="utf-8",
    )
    _write_jsonl(loop_log, [{
        "ts": "2026-07-30T07:08:22",
        "instance_id": instance_id,
        "lag_ms": 5000.0,
        "wedged": True,
        "warn_ms": 500,
        "active_message_ids": ["msg-2", "msg-1", "msg-2"],
        "tasks": [{
            "name": "exec-msg-1",
            "done": False,
            "stack": ["task_supervisor.py:104 in _wrapped"],
        }],
        "threads": [{
            "name": "MainThread",
            "event_loop": True,
            "stack": ["litellm/utils.py:100 in _calculate_usage_per_chunk"],
        }],
    }])
    _write_jsonl(metrics_log, [
        {"ts": "2026-07-30T07:07:53", "process": {"cpu_pct": 7.0}},
        {"ts": "2026-07-30T07:08:23", "process": {"cpu_pct": 22.4}},
    ])
    result = read_instance_events(instance_id, limit=20)
    by_type = {event["type"]: event for event in result["events"]}

    error = by_type["error"]
    assert error["summary"] == "LLM call failed"
    assert error["request_id"] == "req-1"
    assert error["conversation_id"] == "conv-1"
    assert error["message_id"] == "msg-1"
    assert "RuntimeError: boom" in error["detail"]

    wedge = by_type["wedge"]
    assert wedge["lower_bound"] is True
    assert wedge["location"].endswith("_calculate_usage_per_chunk")
    assert wedge["message_id"] is None
    assert wedge["active_message_ids"] == ["msg-1", "msg-2"]
    assert wedge["metrics_before"]["process"]["cpu_pct"] == 7.0
    assert wedge["metrics_after"]["process"]["cpu_pct"] == 22.4

    assert all(source["available"] for source in result["sources"].values())
    assert set(result["sources"]) == {"error_log", "loop_lag", "metrics"}


def test_limit_is_applied_after_newest_first_sort(monkeypatch, tmp_path):
    instance_id = "backend-2"
    error_log, _, _ = _configure_paths(monkeypatch, tmp_path, instance_id)
    error_log.parent.mkdir(parents=True)
    error_log.write_text(
        "2026-07-30 10:00:00,000 - ArtifactFlow - ERROR - "
        "[backend-2|req-old|no-ctx|no-ctx] a.py:f:1 - old\n"
        "2026-07-30 11:00:00,000 - ArtifactFlow - ERROR - "
        "[backend-2|req-new|no-ctx|no-ctx] b.py:g:2 - new\n",
        encoding="utf-8",
    )

    result = read_instance_events(instance_id, limit=1)
    assert [event["summary"] for event in result["events"]] == ["new"]


def test_kind_filter_is_applied_before_limit(monkeypatch, tmp_path):
    """Newer ERROR and soft-lag bursts must not hide a retained hard wedge."""
    instance_id = "backend-filter"
    error_log, loop_log, _ = _configure_paths(monkeypatch, tmp_path, instance_id)
    error_log.parent.mkdir(parents=True)
    error_log.write_text(
        "".join(
            "2026-07-30 12:{minute:02d}:00,000 - ArtifactFlow - ERROR - "
            "[backend-filter|req-{minute}|no-ctx|no-ctx] api.py:f:1 - error-{minute}\n".format(
                minute=minute
            )
            for minute in range(50)
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        loop_log,
        [{
            "ts": "2026-07-30T07:00:00",
            "instance_id": instance_id,
            "lag_ms": 5000,
            "wedged": True,
        }] + [{
            "ts": f"2026-07-30T13:{minute:02d}:00",
            "instance_id": instance_id,
            "lag_ms": 600 + minute,
            "wedged": False,
        } for minute in range(50)],
    )

    wedge_result = read_instance_events(instance_id, limit=1, kind="wedge")
    assert [event["type"] for event in wedge_result["events"]] == ["wedge"]
    assert wedge_result["events"][0]["lag_ms"] == 5000

    lag_result = read_instance_events(instance_id, limit=3, kind="loop_lag")
    assert len(lag_result["events"]) == 3
    assert {event["type"] for event in lag_result["events"]} == {"loop_lag"}
