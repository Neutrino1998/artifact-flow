"""
DeadmanSwitch 单元测试

策略:验证 start/stop 生命周期 + heartbeat 真的会周期 reset(通过捕获
faulthandler.dump_traceback_later 的调用次数)。

不验证 "真的让 loop wedge 后会 dump" — 那是 faulthandler 自身的契约,
且 dump 走 stderr 不易在测试里截获;留给 docs/runbooks/service-hang.md
里的手动 SOP。
"""

import asyncio
from unittest.mock import patch

import pytest

from observability.deadman import DeadmanSwitch


def test_start_stop_lifecycle():
    """简单的 start / stop 跑完不抛。"""

    async def runner():
        deadman = DeadmanSwitch(timeout_ms=200)  # 0.2s timeout → 0.1s heartbeat
        deadman.start()
        try:
            await asyncio.sleep(0.05)
        finally:
            await deadman.stop()

    asyncio.run(runner())


def test_heartbeat_resets_periodically():
    """heartbeat 必须每 timeout/2 调一次 cancel + dump_traceback_later。"""

    async def runner():
        calls = {"cancel": 0, "dump": 0}

        # 真的 enable 一下,确保 faulthandler 起作用;然后 patch 计数。
        import faulthandler

        orig_cancel = faulthandler.cancel_dump_traceback_later
        orig_dump = faulthandler.dump_traceback_later

        def counting_cancel():
            calls["cancel"] += 1
            return orig_cancel()

        def counting_dump(*args, **kwargs):
            calls["dump"] += 1
            return orig_dump(*args, **kwargs)

        with patch.object(faulthandler, "cancel_dump_traceback_later", counting_cancel), \
             patch.object(faulthandler, "dump_traceback_later", counting_dump):
            deadman = DeadmanSwitch(timeout_ms=100)  # 0.1s → heartbeat 每 0.05s
            deadman.start()
            try:
                # 跑 ~0.25s → 应至少 4 次 heartbeat tick(初始 1 次 + 3 次 reset)
                await asyncio.sleep(0.25)
            finally:
                await deadman.stop()

        assert calls["dump"] >= 3, f"expected ≥3 dump_traceback_later calls, got {calls}"
        assert calls["cancel"] >= 2, f"expected ≥2 cancel calls (heartbeat + stop), got {calls}"

    asyncio.run(runner())


def test_stop_cancels_timer():
    """stop 后再没有 dump 调用。"""

    async def runner():
        calls = {"dump": 0}
        import faulthandler

        orig_dump = faulthandler.dump_traceback_later

        def counting_dump(*args, **kwargs):
            calls["dump"] += 1
            return orig_dump(*args, **kwargs)

        with patch.object(faulthandler, "dump_traceback_later", counting_dump):
            deadman = DeadmanSwitch(timeout_ms=100)
            deadman.start()
            await asyncio.sleep(0.15)
            stopped_calls = calls["dump"]
            await deadman.stop()
            # 等一段时间确认无新调用
            await asyncio.sleep(0.2)
            assert calls["dump"] == stopped_calls, (
                f"dump_traceback_later called after stop(): "
                f"before={stopped_calls}, after={calls['dump']}"
            )

    asyncio.run(runner())
