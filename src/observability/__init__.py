"""
Observability — 轻量观测层。

不动 DB schema、不上 Prometheus；业务侧观测复用 MessageEvent，运行时/系统侧观测落 jsonl。

组件:
- jsonl_sink.JsonlSink            轮转写盘 + stdout mirror 的 jsonl 写入器
- watchdog.LoopLagWatchdog        Python 线程,call_soon_threadsafe 测 loop 调度延迟(软退化观测)
- deadman.DeadmanSwitch           faulthandler.dump_traceback_later 周期 reset(硬 wedge 兜底)
- sampler.RuntimeSampler          asyncio task,周期采样 loop_lag / RSS / DB pool / Redis / FD
- heartbeat.HeartbeatWriter       sampler 快照子集多写一份到 Redis(舰队注册表)
- error_counter.ErrorCounterHandler  进程内 ERROR 计数,喂心跳「黄色」信号
"""

from observability.jsonl_sink import JsonlSink
from observability.watchdog import LoopLagWatchdog
from observability.deadman import DeadmanSwitch
from observability.sampler import RuntimeSampler, resolve_mem_limit_bytes
from observability.heartbeat import HeartbeatWriter
from observability import error_counter

__all__ = [
    "JsonlSink",
    "LoopLagWatchdog",
    "DeadmanSwitch",
    "RuntimeSampler",
    "resolve_mem_limit_bytes",
    "HeartbeatWriter",
    "error_counter",
]
