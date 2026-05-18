"""
RuntimeSampler 单元测试

策略:fake runner/db/redis/watchdog,验证一次 sample_once() 调用产出预期形状,
不依赖真实 30s 周期或真实组件。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from observability.jsonl_sink import JsonlSink
from observability.sampler import RuntimeSampler


class _FakeWatchdog:
    def __init__(self, snapshot_data):
        self._snap = snapshot_data

    def snapshot(self):
        return dict(self._snap)


class _FakeRunner:
    def __init__(self, in_flight=2):
        self._tasks = {f"task-{i}": object() for i in range(in_flight)}

    @property
    def active_task_count(self):
        return len(self._tasks)


def test_sample_once_writes_expected_fields(tmp_path):
    """sample_once 写一行,含所有预期顶层字段。"""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=_FakeWatchdog({"p50_ms": 3, "p99_ms": 18, "max_1m_ms": 95, "samples": 30}),
            execution_runner=_FakeRunner(in_flight=2),
            db_manager=None,
            redis_client=None,
            data_dir=str(tmp_path),
            long_task_age_sec=60,
            interval_sec=30,
        )
        snapshot = await sampler.sample_once()
        sink.close()

        # 顶层字段
        for k in ("ts", "loop_lag_ms", "in_flight", "tasks_total", "db_pool",
                  "redis", "process", "data_dir_mb"):
            assert k in snapshot, f"missing field {k}"

        # loop_lag 透传 watchdog snapshot
        assert snapshot["loop_lag_ms"]["p50_ms"] == 3
        assert snapshot["loop_lag_ms"]["p99_ms"] == 18

        # in_flight 来自 runner
        assert snapshot["in_flight"] == 2

        # latest_snapshot 应等于这次的输出
        assert sampler.latest_snapshot() == snapshot

    asyncio.run(runner())


def test_sample_once_appends_to_jsonl(tmp_path):
    """每次 sample_once 都向 jsonl 追加一行。"""
    path = tmp_path / "metrics.jsonl"

    async def runner():
        sink = JsonlSink(path, max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=_FakeRunner(in_flight=0),
            data_dir=str(tmp_path),
        )
        await sampler.sample_once()
        await sampler.sample_once()
        await sampler.sample_once()
        sink.close()

    asyncio.run(runner())

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert "ts" in obj


def test_no_watchdog_means_empty_loop_lag(tmp_path):
    """watchdog=None 时 loop_lag_ms 是空 dict,不抛。"""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink, watchdog=None, execution_runner=None, data_dir=str(tmp_path)
        )
        snapshot = await sampler.sample_once()
        sink.close()
        assert snapshot["loop_lag_ms"] == {}

    asyncio.run(runner())


def test_threshold_warn_on_high_rss(tmp_path, caplog):
    """RSS 超过 80% mem_limit 时打 WARN。"""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        # mem_limit = 100 字节 → 任何 RSS 都超 80%(即触发)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=None,
            data_dir=str(tmp_path),
            mem_limit_bytes=100,
        )
        # caplog 抓 WARN
        import logging as _logging
        with caplog.at_level(_logging.WARNING, logger="ArtifactFlow"):
            await sampler.sample_once()
        sink.close()

        warns = [r for r in caplog.records if r.levelname == "WARNING" and "RSS" in r.message]
        assert warns, "expected RSS-over-limit WARN log"

    asyncio.run(runner())


def test_sampler_start_stop(tmp_path):
    """start / stop 生命周期。"""

    async def runner():
        sink = JsonlSink(tmp_path / "metrics.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        sampler = RuntimeSampler(
            sink=sink,
            watchdog=None,
            execution_runner=None,
            data_dir=str(tmp_path),
            interval_sec=1,  # 1s 周期,但我们 stop 得早
        )
        sampler.start()
        await asyncio.sleep(0.05)
        await sampler.stop()
        # idempotent
        await sampler.stop()
        sink.close()

    asyncio.run(runner())
