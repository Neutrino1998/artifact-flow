"""
Regression tests for the four reviewer findings on PR-obs-lite:
  - tasks_long_running counts actual long-running tasks
  - mem_limit resolution via env override + cgroup v2 + cgroup v1
  - sampler RSS high-water WARN actually fires when mem_limit is wired
  - data_dir size scan runs off the event loop
  - script DB URL precedence matches the application
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from observability.jsonl_sink import JsonlSink
from observability.sampler import RuntimeSampler, resolve_mem_limit_bytes


# ============================================================
# 1. tasks_long_running counting
# ============================================================


class _FakeRunner:
    """Mirror the minimal shape RuntimeSampler reads on ExecutionRunner."""

    def __init__(self, *, long_running: int, in_flight: int):
        self._tasks = {f"t-{i}": object() for i in range(in_flight)}
        self._long_running = long_running

    @property
    def active_task_count(self) -> int:
        return len(self._tasks)

    def long_running_count(self, threshold_sec: float) -> int:
        return self._long_running


def test_execution_runner_long_running_count():
    """ExecutionRunner.long_running_count returns ages over threshold."""
    from api.services.execution_runner import ExecutionRunner

    runner = ExecutionRunner(max_concurrent=4)
    now = time.monotonic()
    # synthesize 3 tasks: 2 over threshold, 1 fresh
    runner._task_started_at = {
        "old-1": now - 120,
        "old-2": now - 90,
        "fresh-3": now - 5,
    }
    assert runner.long_running_count(60) == 2     # only old-1 (120s) + old-2 (90s)
    assert runner.long_running_count(1) == 3      # all three exceed 1s
    assert runner.long_running_count(1000) == 0   # nothing exceeds 1000s


def test_execution_runner_long_running_empty():
    """No tracked tasks → 0, no exception."""
    from api.services.execution_runner import ExecutionRunner

    runner = ExecutionRunner(max_concurrent=4)
    assert runner.long_running_count(60) == 0


def test_sampler_reads_long_running_from_runner(tmp_path):
    """Sampler.tasks_long_running pulls from runner.long_running_count."""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=_FakeRunner(long_running=2, in_flight=3),
            data_dir=str(tmp_path),
            long_task_age_sec=60,
        )
        snap = await sampler.sample_once()
        sink.close()
        assert snap["in_flight"] == 3
        assert snap["tasks_long_running"] == 2

    asyncio.run(runner())


def test_sampler_long_running_graceful_when_method_missing(tmp_path):
    """Old runners without long_running_count → 0, not exception."""

    class _LegacyRunner:
        def __init__(self):
            self._tasks = {"a": object()}

        @property
        def active_task_count(self):
            return 1

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink, watchdog=None,
            execution_runner=_LegacyRunner(), data_dir=str(tmp_path),
        )
        snap = await sampler.sample_once()
        sink.close()
        assert snap["in_flight"] == 1
        assert snap["tasks_long_running"] == 0

    asyncio.run(runner())


# ============================================================
# 2. mem_limit resolution (env > cgroup v2 > cgroup v1 > None)
# ============================================================


def test_resolve_mem_limit_explicit_wins():
    """Explicit mb wins regardless of cgroup contents."""
    assert resolve_mem_limit_bytes(2048) == 2048 * 1024 * 1024


def test_resolve_mem_limit_cgroup_v2(tmp_path, monkeypatch):
    """cgroup v2 path read."""
    fake_v2 = tmp_path / "memory.max"
    fake_v2.write_text("2147483648\n")
    monkeypatch.setattr("observability.sampler._CGROUP_V2_MEMORY_MAX", str(fake_v2))
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(tmp_path / "nonexistent")
    )
    assert resolve_mem_limit_bytes(0) == 2147483648


def test_resolve_mem_limit_cgroup_v2_max_sentinel(tmp_path, monkeypatch):
    """v2 "max" string means unlimited → fall through."""
    fake_v2 = tmp_path / "memory.max"
    fake_v2.write_text("max\n")
    fake_v1 = tmp_path / "memory.limit_in_bytes"
    fake_v1.write_text("4294967296\n")
    monkeypatch.setattr("observability.sampler._CGROUP_V2_MEMORY_MAX", str(fake_v2))
    monkeypatch.setattr("observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(fake_v1))
    assert resolve_mem_limit_bytes(0) == 4294967296


def test_resolve_mem_limit_cgroup_v1_fallback(tmp_path, monkeypatch):
    """No v2, v1 has value."""
    fake_v1 = tmp_path / "memory.limit_in_bytes"
    fake_v1.write_text("1073741824\n")
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V2_MEMORY_MAX", str(tmp_path / "nonexistent")
    )
    monkeypatch.setattr("observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(fake_v1))
    assert resolve_mem_limit_bytes(0) == 1073741824


def test_resolve_mem_limit_v1_unlimited_sentinel(tmp_path, monkeypatch):
    """v1 unlimited (~9.2 EiB sentinel) → None."""
    fake_v1 = tmp_path / "memory.limit_in_bytes"
    fake_v1.write_text("9223372036854771712\n")  # actual k8s/docker unlimited value
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V2_MEMORY_MAX", str(tmp_path / "nonexistent")
    )
    monkeypatch.setattr("observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(fake_v1))
    assert resolve_mem_limit_bytes(0) is None


def test_resolve_mem_limit_all_missing(tmp_path, monkeypatch):
    """No env, no cgroup → None (sampler will silently skip RSS WARN)."""
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V2_MEMORY_MAX", str(tmp_path / "no-v2")
    )
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(tmp_path / "no-v1")
    )
    assert resolve_mem_limit_bytes(0) is None


def test_resolve_mem_limit_garbage_swallowed(tmp_path, monkeypatch):
    """Unparseable cgroup file is treated as missing."""
    bad = tmp_path / "memory.max"
    bad.write_text("not-an-int\n")
    monkeypatch.setattr("observability.sampler._CGROUP_V2_MEMORY_MAX", str(bad))
    monkeypatch.setattr(
        "observability.sampler._CGROUP_V1_MEMORY_LIMIT", str(tmp_path / "no-v1")
    )
    assert resolve_mem_limit_bytes(0) is None


# ============================================================
# 3. Sampler RSS WARN fires when mem_limit is wired
# ============================================================


def test_sampler_rss_warn_fires_when_mem_limit_set(tmp_path, caplog):
    """If mem_limit_bytes is passed, RSS > 80% triggers WARN.

    We force the limit to 100 bytes so any real RSS dwarfs 80%, guaranteeing
    the alert path runs end-to-end.
    """
    import logging

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=None,
            data_dir=str(tmp_path),
            mem_limit_bytes=100,
        )
        with caplog.at_level(logging.WARNING, logger="ArtifactFlow"):
            await sampler.sample_once()
        sink.close()

        warns = [r for r in caplog.records if r.levelname == "WARNING" and "RSS" in r.message]
        assert warns, "expected RSS-over-limit WARN, got no records"

    asyncio.run(runner())


def test_sampler_rss_warn_silent_when_mem_limit_none(tmp_path, caplog):
    """No mem_limit → no RSS WARN (current behavior, pre-fix sentinel)."""
    import logging

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=None,
            data_dir=str(tmp_path),
            mem_limit_bytes=None,
        )
        with caplog.at_level(logging.WARNING, logger="ArtifactFlow"):
            await sampler.sample_once()
        sink.close()

        warns = [r for r in caplog.records if r.levelname == "WARNING" and "RSS" in r.message]
        assert not warns, f"expected no RSS WARN when mem_limit is None, got {warns}"

    asyncio.run(runner())


# ============================================================
# 4. data_dir scan goes through asyncio.to_thread
# ============================================================


def test_data_dir_scan_runs_off_event_loop(tmp_path):
    """sample_once must hand the dir scan to another thread so a slow scan
    on a large volume does not become a loop-lag source itself."""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink, watchdog=None, execution_runner=None, data_dir=str(tmp_path)
        )

        loop_thread_id = threading.get_ident()
        scan_thread_id_holder: dict[str, int] = {}

        original = sampler._data_dir_size_mb

        def tracking_scan():
            scan_thread_id_holder["tid"] = threading.get_ident()
            return original()

        sampler._data_dir_size_mb = tracking_scan

        await sampler.sample_once()
        sink.close()

        assert "tid" in scan_thread_id_holder, "data_dir scan never ran"
        assert scan_thread_id_holder["tid"] != loop_thread_id, (
            "data_dir scan ran on the event-loop thread — must be on to_thread"
        )

    asyncio.run(runner())


# ============================================================
# 5. Script DB URL precedence (DATABASE_URLS first, mirror config.effective_database_url)
# ============================================================


def _load_script_module():
    """Load scripts/observability_report.py as a module without invoking main()."""
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "observability_report.py"
    spec = importlib.util.spec_from_file_location("observability_report", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_db_url_prefers_database_urls(monkeypatch):
    """When both env vars are set, the script must follow DATABASE_URLS first
    (matching config.effective_database_url) — otherwise the report queries
    the wrong DB in production."""
    monkeypatch.setenv("ARTIFACTFLOW_DATABASE_URLS", "postgresql://primary/db,postgresql://replica/db")
    monkeypatch.setenv("ARTIFACTFLOW_DATABASE_URL", "postgresql://OTHER/db")

    module = _load_script_module()
    resolved = module._resolve_engine_url()
    # First entry of URLS wins (with async→sync driver swap)
    assert "primary" in resolved
    assert "OTHER" not in resolved


def test_script_db_url_falls_back_to_database_url(monkeypatch):
    """DATABASE_URLS empty → use DATABASE_URL."""
    monkeypatch.delenv("ARTIFACTFLOW_DATABASE_URLS", raising=False)
    monkeypatch.setenv("ARTIFACTFLOW_DATABASE_URL", "postgresql://fallback/db")

    module = _load_script_module()
    resolved = module._resolve_engine_url()
    assert "fallback" in resolved


def test_script_db_url_default_sqlite(monkeypatch):
    """Neither env set → default sqlite path."""
    monkeypatch.delenv("ARTIFACTFLOW_DATABASE_URLS", raising=False)
    monkeypatch.delenv("ARTIFACTFLOW_DATABASE_URL", raising=False)

    module = _load_script_module()
    resolved = module._resolve_engine_url()
    assert resolved.startswith("sqlite")


def test_script_db_url_strips_async_drivers(monkeypatch):
    """pd.read_sql needs sync drivers — async suffixes must be stripped."""
    monkeypatch.setenv(
        "ARTIFACTFLOW_DATABASE_URLS", "postgresql+asyncpg://host/db"
    )
    monkeypatch.delenv("ARTIFACTFLOW_DATABASE_URL", raising=False)

    module = _load_script_module()
    resolved = module._resolve_engine_url()
    assert "+asyncpg" not in resolved
    assert "psycopg2" in resolved
