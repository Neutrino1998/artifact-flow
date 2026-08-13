"""
LoopLagWatchdog — 事件循环 lag 软观测(Python 线程)

定位:**软退化**观测器。覆盖"loop 调度有 await 但被拖慢"的场景,产出可统计的
loop_lag 分布。

失效面(必须明确写下):
- 本组件跑在 Python `threading.Thread` 里,持 / 等 GIL。如果某个 C 扩展持有 GIL
  不释放，所有 Python 线程一起 `futex_wait`,本线程也
  会**与事件循环一起睡死**,产不出数据。
- **该场景必须由 deadman.DeadmanSwitch 兜底**(C 线程 dump,不要 GIL)。两者
  互补,目的不同,都留。

设计要点:
- 每 `interval_sec`(默认 1s)通过 `loop.call_soon_threadsafe` 投一个回调,记录
  "投递 → 执行" 的延迟即为 loop lag
- 滚动窗口存 p50 / p99 / 1 分钟 max(供 /admin/runtime 拉)
- 超 `warn_ms`(默认 500ms)时先抓事件循环线程栈与 `asyncio.all_tasks()`,恢复后
  写一行到 loop-lag.jsonl(便于看到阻塞当下的位置与同时活跃的任务)
- **失败一律吞**(对齐 jsonl_sink 同款 observer-must-not-disturb-observee 原则)
- 不在 asyncio task 里 — loop 卡死时自己也会被卡(不与所观测对象共栈)
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import traceback
from collections import deque
from typing import Optional

from observability.jsonl_sink import JsonlSink
from utils.instance import INSTANCE_ID
from utils.logger import get_logger
from utils.time import utc_now

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
        # Watchdog is constructed from FastAPI lifespan on the running loop
        # thread.  Freeze that public thread identity here instead of depending
        # on CPython asyncio's private ``loop._thread_id`` (absent on uvloop).
        self._event_loop_thread_id = threading.get_ident()

        # 滚动窗口(只在 watchdog 线程内读写,无锁)
        self._samples: deque[float] = deque(maxlen=self._MAX_WINDOW_SAMPLES)

        # snapshot(对外暴露给 sampler / /admin/runtime;原子赋值,无锁)
        self._snapshot: dict = {"p50_ms": 0, "p99_ms": 0, "max_1m_ms": 0, "samples": 0}

        # 最近一次 wedge/超阈事件摘要（供心跳读）：watchdog 线程写、loop
        # 线程读,dict 整体赋值原子、无锁(同 _snapshot)。None = 本进程从未抓到过。
        self._last_wedge: Optional[dict] = None

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

    def last_wedge(self) -> Optional[dict]:
        """供心跳读取最近一次 wedge/超阈事件摘要；从未抓到过返回 None。

        栈明细仍只落 loop-lag.jsonl(取证用),这里只带实例卡片快速定位要用的
        轻摘要 {ts, lag_ms, wedged, location?, active_message_ids}。
        """
        return dict(self._last_wedge) if self._last_wedge else None

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
        completed_at: Optional[float] = None

        def _callback() -> None:
            nonlocal completed_at
            completed_at = time.monotonic()
            done.set()

        try:
            self._loop.call_soon_threadsafe(_callback)
        except RuntimeError:
            # loop 已关闭等
            return

        # 先只等到 soft-lag 阈值。若此时回调仍未执行,必须趁事件循环还被占住时
        # 立刻抓线程栈;等它恢复后再抓只会看到外围 await 边界,真正的同步阻塞
        # 函数已经离栈。随后继续等到 hard-wedge 上限,维持原有分类语义。
        warn_timeout = max(self._warn_ms / 1000.0, 0.0)
        hard_timeout = max(warn_timeout * 4, 5.0)
        threshold_threads: Optional[list[dict]] = None
        threshold_tasks: Optional[list[dict]] = None

        if not done.wait(timeout=warn_timeout):
            try:
                # 线程栈最容易随 loop 恢复而消失,优先于 task 清单采集。
                threshold_threads = self._collect_thread_stacks()
            except Exception:
                threshold_threads = []
            try:
                threshold_tasks = self._collect_task_stacks()
            except Exception:
                threshold_tasks = []

        elapsed = time.monotonic() - sent
        remaining = max(0.0, hard_timeout - elapsed)
        if completed_at is None:
            done.wait(timeout=remaining)
        if (
            completed_at is None
            or completed_at - sent >= hard_timeout
        ):
            # 真的卡了 — 记录一条 wedge 事件,继续下一轮(不阻塞自己)
            # hard wedge 仍在 5s 点重新抓栈,优先展示持续占住 loop 的位置。
            self._record_wedge(hard_timeout * 1000.0, wedged=True)
            return

        lag_ms = (completed_at - sent) * 1000.0
        self._samples.append(lag_ms)
        self._update_snapshot()

        if lag_ms >= self._warn_ms:
            self._record_wedge(
                lag_ms,
                wedged=False,
                tasks_info=threshold_tasks,
                threads_info=threshold_threads,
            )

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

    def _record_wedge(
        self,
        lag_ms: float,
        *,
        wedged: bool,
        tasks_info: Optional[list[dict]] = None,
        threads_info: Optional[list[dict]] = None,
    ) -> None:
        """写一行 loop-lag.jsonl,优先使用阈值当下预抓的 task/thread 栈。"""
        recorded_at = utc_now().isoformat()
        if tasks_info is None:
            try:
                tasks_info = self._collect_task_stacks()
            except Exception:
                tasks_info = []

        # asyncio Task.get_stack() 在协程正执行同步 Python 代码时常只暴露 task
        # 的外层 await 边界。soft lag 使用 warn 阈值当下预抓的线程栈;hard wedge
        # 则在 timeout 点从 sys._current_frames() 现抓,才能看到真正占住 event-loop
        # 线程的函数。C 扩展持 GIL 时本 Python 线程也跑不到,仍由 faulthandler
        # deadman 的 C 线程兜底。
        if threads_info is None and wedged:
            try:
                threads_info = self._collect_thread_stacks()
            except Exception:
                threads_info = []
        elif threads_info is None:
            threads_info = []

        loop_thread = next(
            (thread for thread in threads_info if thread.get("event_loop")), None
        )
        location = None
        if loop_thread and loop_thread.get("stack"):
            location = loop_thread["stack"][-1]
        active_message_ids = sorted({
            str(task.get("name"))[len("exec-") :]
            for task in tasks_info
            if str(task.get("name") or "").startswith("exec-msg-")
        })

        # 只有**硬 wedge**(回调彻底不来)才留 last_wedge 摘要。摘要在进程生命周期内
        # 保留供卡片展示历史事件，但判黄只看近期窗口；软告警(lag≥warn 但回调仍来)
        # 是 routine 抖动(GC/冷 import)，可见性由 loop_lag.max_1m_ms(60s 窗口自愈)
        # 承载。jsonl 两者都记，取证不受影响。
        if wedged:
            self._last_wedge = {
                "ts": recorded_at,
                "lag_ms": round(lag_ms, 1),
                "wedged": True,
                "location": location,
                "active_message_ids": active_message_ids,
            }

        try:
            self._sink.write({
                "ts": recorded_at,
                # 目录已按实例分,但记录内也带:文件被拷走聚合后目录信息即丢
                "instance_id": INSTANCE_ID,
                "lag_ms": round(lag_ms, 1),
                "wedged": wedged,
                "warn_ms": self._warn_ms,
                "active_message_ids": active_message_ids,
                "tasks": tasks_info,
                "threads": threads_info,
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

    def _collect_thread_stacks(self) -> list[dict]:
        """Capture bounded Python stacks for every live interpreter thread.

        The loop thread identity is frozen when the watchdog is constructed on
        FastAPI's running event loop, so this works across asyncio and uvloop.
        """
        frames = sys._current_frames()
        names = {
            thread.ident: thread.name
            for thread in threading.enumerate()
            if thread.ident is not None
        }
        out: list[dict] = []
        for thread_id, frame in frames.items():
            try:
                extracted = traceback.extract_stack(frame, limit=self._STACK_FRAMES)
                out.append({
                    "name": names.get(thread_id, f"thread-{thread_id}"),
                    "event_loop": thread_id == self._event_loop_thread_id,
                    "stack": [
                        f"{item.filename}:{item.lineno} in {item.name}"
                        for item in extracted
                    ],
                })
            except Exception:
                continue
        return out
