"""
Observability — 轻量观测层（PR-obs-lite）

详细设计见 docs/_archive/ops/incident-2026-05-14-fix-plan.md → PR-obs-lite。
不动 DB schema、不上 Prometheus；业务侧观测复用 MessageEvent，运行时/系统侧观测落 jsonl。

四个组件:
- jsonl_sink.JsonlSink            轮转写盘 + stdout mirror 的 jsonl 写入器
- watchdog.LoopLagWatchdog        Python 线程,call_soon_threadsafe 测 loop 调度延迟(软退化观测)
- deadman.DeadmanSwitch           faulthandler.dump_traceback_later 周期 reset(硬 wedge 兜底)
- sampler.RuntimeSampler          asyncio task,周期采样 loop_lag / RSS / DB pool / Redis / FD
"""

from observability.jsonl_sink import JsonlSink
from observability.watchdog import LoopLagWatchdog
from observability.deadman import DeadmanSwitch
from observability.sampler import RuntimeSampler

__all__ = [
    "JsonlSink",
    "LoopLagWatchdog",
    "DeadmanSwitch",
    "RuntimeSampler",
]
