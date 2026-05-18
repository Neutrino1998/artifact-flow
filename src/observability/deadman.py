"""
DeadmanSwitch — `faulthandler` 硬 wedge 兜底

定位:**硬 wedge** 兜底。专门覆盖 watchdog 失效的场景:CPython GIL 被 C 扩展持
续持有,所有 Python 线程一起 `futex_wait`(本次 2026-05-14 事故同款)。

原理:
- `faulthandler.dump_traceback_later(timeout)` 用 CPython 自己起的**纯 C 线程**
  (`PyThread_start_new_thread` + `sem_timedwait`)实现倒计时,到点 `_Py_DumpTraceback()`
  把所有线程的 Python 栈打到 stderr — 这个 dump 路径**不获取 GIL**,专为
  "interpreter hung" 设计
- 我们把它当 deadman switch 用:一个独立 asyncio task 周期 reset 计时器
  - loop 健康 → reset 一直成功 → 计时器永远到不了 timeout → 不产生噪声 dump
  - loop wedge(同步 CPU / C 扩展持 GIL / 死锁均可) → heartbeat 跑不到 →
    C 线程到点 dump → docker logs backend 拉得到

cadence:每 `timeout / 2` reset 一次,留 50% 余量。默认 10s timeout → 每 5s reset。
明显大于正常工具最长耗时 + 单次 LLM 调用,小于事故 96 分钟若干个数量级。

faulthandler.enable() 必须在 main.py lifespan 最早期调用(在 deadman 启动前)
— 这个组件假定 enable 已生效。
"""

from __future__ import annotations

import asyncio
import faulthandler
import sys
from typing import Optional

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class DeadmanSwitch:
    """
    周期 reset `faulthandler.dump_traceback_later` 的 deadman switch

    用法:
        deadman = DeadmanSwitch(timeout_ms=10000)
        deadman.start()
        ...
        await deadman.stop()
    """

    def __init__(self, timeout_ms: int = 10000):
        self._timeout_sec = timeout_ms / 1000.0
        self._heartbeat_interval = self._timeout_sec / 2.0
        self._task: Optional[asyncio.Task] = None
        self._enabled = False

    def start(self) -> None:
        """启动 heartbeat task。在 lifespan 内调用。"""
        if self._task is not None:
            return
        try:
            # 首次设定 timer;heartbeat 之后会持续 reset
            faulthandler.dump_traceback_later(
                self._timeout_sec, repeat=False, file=sys.stderr
            )
            self._enabled = True
        except Exception:
            # enable 失败 swallow + WARN,不挂应用启动
            logger.exception("DeadmanSwitch: faulthandler enable failed; skipping")
            return

        self._task = asyncio.create_task(self._heartbeat(), name="deadman-heartbeat")
        logger.info(
            f"DeadmanSwitch started "
            f"(timeout={self._timeout_sec:.1f}s, heartbeat={self._heartbeat_interval:.1f}s)"
        )

    async def stop(self) -> None:
        """关闭 heartbeat + cancel 当前 timer。在 lifespan shutdown 调用。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._enabled:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
            self._enabled = False
        logger.info("DeadmanSwitch stopped")

    async def _heartbeat(self) -> None:
        """周期 reset faulthandler 计时器;只要本协程能跑就证明 loop 健康。"""
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                faulthandler.cancel_dump_traceback_later()
                faulthandler.dump_traceback_later(
                    self._timeout_sec, repeat=False, file=sys.stderr
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # 任何异常都不能让 heartbeat 死掉(死掉 = 下一个 timeout 就 dump 噪声)。
                # 留一条 WARN 便于排查 deadman 自身问题。
                logger.exception("DeadmanSwitch heartbeat tick failed; continuing")
