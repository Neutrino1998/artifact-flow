"""
LoopLagWatchdog — 事件循环 lag 软观测(Python 线程)

定位:**软退化**观测器。覆盖"loop 调度有 await 但被拖慢"的场景,产出可统计的
loop_lag 分布。

失效面(必须明确写下):
- 本组件跑在 Python `threading.Thread` 里,持 / 等 GIL。如果某个 C 扩展持有 GIL
  不释放(本次事故 fuzzysearch),所有 Python 线程一起 `futex_wait`,本线程也
  会**与事件循环一起睡死**,产不出数据。
- **该场景必须由 deadman.DeadmanSwitch 兜底**(C 线程 dump,不要 GIL)。两者
  互补,目的不同,都留。

设计要点:
- 每 `interval_sec`(默认 1s)通过 `loop.call_soon_threadsafe` 投一个回调,记录
  "投递 → 执行" 的延迟即为 loop lag
- 滚动窗口存 p50 / p99 / 1 分钟 max(供 /admin/runtime 拉)
- 超 `warn_ms`(默认 500ms)即写一行到 loop-lag.jsonl,附 `asyncio.all_tasks()` 各
  task 的栈截断(便于事后定位是谁拖的 loop)
- **失败一律吞**(对齐 jsonl_sink 同款 observer-must-not-disturb-observee 原则)
- 不在 asyncio task 里 — loop 卡死时自己也会被卡(不与所观测对象共栈)
"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from observability.jsonl_sink import JsonlSink
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class LoopLagWatchdog:
    """
    监测 asyncio 事件循环 lag 的 Python 线程

    用法:
        watchdog = LoopLagWatchdog(loop, sink, warn_ms=500, interval_sec=1.0)
        watchdog.start()
        ...
        watchdog.stop()
    """

    # 1 分钟 max 滚动窗口大小:interval=1s × 60 = 60 个样本
    _MAX_WINDOW_SAMPLES = 60

    # loop-lag.jsonl 里每个 task 栈截断帧数(防一条记录过大)
    _STACK_FRAMES = 8

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        sink: JsonlSink,
        *,
        warn_ms: int = 500,
        interval_sec: float = 1.0,
    ):
        self._loop = loop
        self._sink = sink
        self._warn_ms = warn_ms
        self._interval = interval_sec

        # 滚动窗口(只在 watchdog 线程内读写,无锁)
        self._samples: deque[float] = deque(maxlen=self._MAX_WINDOW_SAMPLES)

        # snapshot(对外暴露给 sampler / /admin/runtime;原子赋值,无锁)
        self._snapshot: dict = {"p50_ms": 0, "p99_ms": 0, "max_1m_ms": 0, "samples": 0}

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="loop-lag-watchdog", daemon=True
        )
        self._thread.start()
        logger.info(
            f"LoopLagWatchdog started "
            f"(warn_ms={self._warn_ms}, interval={self._interval}s)"
        )

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("LoopLagWatchdog stopped")

    def snapshot(self) -> dict:
        """供 sampler / /admin/runtime 读取当前 loop_lag 滚动统计。"""
        return dict(self._snapshot)

    # ── 内部实现 ─────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._measure_once()
            except Exception:
                # observer 必须吞,但留一条 WARN 便于排查 watchdog 自身 bug
                logger.exception("LoopLagWatchdog measurement failed")
            # 用 Event.wait 而非 sleep,stop() 即时响应
            self._stop.wait(self._interval)

    def _measure_once(self) -> None:
        if self._loop.is_closed():
            return

        # 用 threading.Event 在 watchdog 线程等待 loop 线程回调
        done = threading.Event()
        sent = time.monotonic()

        def _callback() -> None:
            done.set()

        try:
            self._loop.call_soon_threadsafe(_callback)
        except RuntimeError:
            # loop 已关闭等
            return

        # 等待 loop 调度到回调;给一个明显大于 warn 阈值的上限避免 watchdog 自身卡死
        # (loop wedge 场景下回调永远不来,但我们要继续采样)
        timeout = max(self._warn_ms / 1000.0 * 4, 5.0)
        if not done.wait(timeout=timeout):
            # 真的卡了 — 记录一条 wedge 事件,继续下一轮(不阻塞自己)
            self._record_wedge(timeout * 1000.0, wedged=True)
            return

        lag_ms = (time.monotonic() - sent) * 1000.0
        self._samples.append(lag_ms)
        self._update_snapshot()

        if lag_ms >= self._warn_ms:
            self._record_wedge(lag_ms, wedged=False)

    def _update_snapshot(self) -> None:
        if not self._samples:
            return
        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)
        # p50 / p99:nearest-rank。样本数 < 100 时 p99 退化为 max,可接受。
        p50 = sorted_samples[int(n * 0.5)]
        p99 = sorted_samples[min(int(n * 0.99), n - 1)]
        self._snapshot = {
            "p50_ms": round(p50, 1),
            "p99_ms": round(p99, 1),
            "max_1m_ms": round(max(sorted_samples), 1),
            "samples": n,
        }

    def _record_wedge(self, lag_ms: float, *, wedged: bool) -> None:
        """采集 asyncio.all_tasks() 各 task 栈截断,写一行 loop-lag.jsonl。"""
        try:
            tasks_info = self._collect_task_stacks()
        except Exception:
            tasks_info = []

        try:
            self._sink.write({
                "ts": datetime.now(timezone.utc).isoformat(),
                "lag_ms": round(lag_ms, 1),
                "wedged": wedged,
                "warn_ms": self._warn_ms,
                "tasks": tasks_info,
            })
        except Exception:
            pass

        if wedged:
            logger.warning(
                f"Event loop appears wedged (no response in {lag_ms:.0f}ms) — "
                f"see loop-lag.jsonl + faulthandler dump"
            )
        else:
            logger.warning(
                f"Event loop lag {lag_ms:.0f}ms exceeded "
                f"warn threshold {self._warn_ms}ms"
            )

    def _collect_task_stacks(self) -> list[dict]:
        """从 watchdog 线程读 loop 的 all_tasks()。

        `asyncio.all_tasks(loop)` 是线程安全的(读 weakset)。task.get_stack() 也是
        线程安全(读已 frozen frame),可在外线程调用。
        """
        try:
            tasks = asyncio.all_tasks(self._loop)
        except RuntimeError:
            return []

        out: list[dict] = []
        for task in tasks:
            try:
                frames = task.get_stack(limit=self._STACK_FRAMES)
                stack_lines = []
                for frame in frames:
                    code = frame.f_code
                    stack_lines.append(
                        f"{code.co_filename}:{frame.f_lineno} in {code.co_name}"
                    )
                out.append({
                    "name": task.get_name(),
                    "done": task.done(),
                    "stack": stack_lines,
                })
            except Exception:
                continue
        return out
