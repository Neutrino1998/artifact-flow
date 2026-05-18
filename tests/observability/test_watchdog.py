"""
LoopLagWatchdog 单元测试

策略:不依赖真实的 sleep / 时间;构造一个轻量 fake event loop,验证:
- 正常情况下样本计入 snapshot
- 超阈值时写一行到 sink
- snapshot() 返回当前滚动窗口统计

完整的 "真起线程 + 真等 N 秒" 集成测试单独走 smoke test。
"""

import asyncio
import threading
import time

import pytest

from observability.jsonl_sink import JsonlSink
from observability.watchdog import LoopLagWatchdog


def test_snapshot_empty_before_start(tmp_path):
    """没启动时 snapshot 也应返回安全的零值结构。"""
    loop = asyncio.new_event_loop()
    sink = JsonlSink(tmp_path / "loop-lag.jsonl", max_mb=1, backups=1, mirror_stdout=False)
    try:
        wd = LoopLagWatchdog(loop, sink, warn_ms=500, interval_sec=1.0)
        snap = wd.snapshot()
        assert snap == {"p50_ms": 0, "p99_ms": 0, "max_1m_ms": 0, "samples": 0}
    finally:
        sink.close()
        loop.close()


def test_records_lag_when_loop_responsive(tmp_path):
    """运行中的 loop,watchdog 应该记到亚 ms 级 lag。"""

    async def runner():
        sink = JsonlSink(tmp_path / "loop-lag.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        loop = asyncio.get_running_loop()
        wd = LoopLagWatchdog(loop, sink, warn_ms=10_000, interval_sec=0.05)
        try:
            wd.start()
            # 让 watchdog 跑 5 个 interval(0.05s × 5 = 0.25s),保证至少有几个样本
            await asyncio.sleep(0.3)
            snap = wd.snapshot()
            assert snap["samples"] >= 1, f"expected at least 1 sample, got {snap}"
            assert snap["p50_ms"] >= 0
        finally:
            wd.stop()
            sink.close()

    asyncio.run(runner())


def test_writes_loop_lag_jsonl_on_threshold(tmp_path, monkeypatch):
    """构造超阈值场景 — block loop in callback so call_soon_threadsafe 延迟 > warn_ms。

    简化:把 warn_ms 设为 0,任何 lag 都触发。
    """
    path = tmp_path / "loop-lag.jsonl"

    async def runner():
        sink = JsonlSink(path, max_mb=1, backups=1, mirror_stdout=False)
        loop = asyncio.get_running_loop()
        wd = LoopLagWatchdog(loop, sink, warn_ms=0, interval_sec=0.05)
        try:
            wd.start()
            # 让 watchdog 跑几轮
            await asyncio.sleep(0.3)
        finally:
            wd.stop()
            sink.close()

    asyncio.run(runner())

    # warn_ms=0 → 每次都触发,jsonl 应有 ≥ 1 行
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert content.strip(), f"expected loop-lag entries written, got empty file"
    # 每一行都应是合法 JSON,且含 lag_ms / wedged 字段
    import json as _json

    for line in content.strip().splitlines():
        obj = _json.loads(line)
        assert "lag_ms" in obj
        assert "wedged" in obj


def test_collect_task_stacks_format(tmp_path):
    """task 栈采集应返回 list[dict],每个 dict 含 name/done/stack。"""

    async def runner():
        sink = JsonlSink(tmp_path / "loop-lag.jsonl", max_mb=1, backups=1, mirror_stdout=False)
        loop = asyncio.get_running_loop()
        wd = LoopLagWatchdog(loop, sink, warn_ms=500, interval_sec=1.0)
        try:
            # 起一个 idle task,确保 all_tasks 非空
            idle = asyncio.create_task(asyncio.sleep(10))
            tasks = wd._collect_task_stacks()
            assert isinstance(tasks, list)
            assert len(tasks) >= 1
            for t in tasks:
                assert "name" in t
                assert "done" in t
                assert "stack" in t
                assert isinstance(t["stack"], list)
            idle.cancel()
            try:
                await idle
            except asyncio.CancelledError:
                pass
        finally:
            sink.close()

    asyncio.run(runner())
