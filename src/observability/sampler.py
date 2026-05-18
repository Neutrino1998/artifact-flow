"""
RuntimeSampler — 周期采样到 jsonl

每 `OBS_SAMPLE_INTERVAL_SEC` 采一次,写一行 JSON 到 `data/observability/metrics.jsonl`,
字段示例:

    {"ts": "...", "loop_lag_ms": {"p50": 3, "p99": 18, "max_1m": 95},
     "in_flight": 2, "tasks_total": 134, "tasks_long_running": 0,
     "db_pool": {"in_use": 3, "overflow": 0, "waiters": 0},
     "redis": {"used_mb": 87},
     "process": {"rss_mb": 512, "cpu_pct": 12, "open_fds": 87},
     "data_dir_mb": 1843}

逼近上限(RSS > 80% mem_limit / Redis used > 80% maxmemory / DB pool overflow > 0 /
open_fds > 80% ulimit)→ 额外打一行 WARN 日志,loud failure 而非 LRU 驱逐。

sampler 自身异常一律吞 — observer 不能拖累 observee。
"""

from __future__ import annotations

import asyncio
import os
import resource
import time
from pathlib import Path
from typing import Any, Optional

import psutil

from utils.time import utc_now

from observability.jsonl_sink import JsonlSink
from observability.watchdog import LoopLagWatchdog
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class RuntimeSampler:
    """
    周期采样运行时指标的 asyncio task

    用法:
        sampler = RuntimeSampler(
            sink=metrics_sink,
            watchdog=watchdog,
            execution_runner=runner,
            db_manager=db,
            redis_client=redis,
            long_task_age_sec=60,
            interval_sec=30,
        )
        sampler.start()
        ...
        await sampler.stop()
    """

    # 高水位告警阈值(对齐 fix plan §注入点 #3):RSS / FD 用 80% ulimit,redis 自配
    _RSS_WARN_RATIO = 0.80
    _FD_WARN_RATIO = 0.80
    _REDIS_USED_WARN_RATIO = 0.80

    def __init__(
        self,
        *,
        sink: JsonlSink,
        watchdog: Optional[LoopLagWatchdog],
        execution_runner: Any = None,
        db_manager: Any = None,
        redis_client: Any = None,
        data_dir: str = "data",
        long_task_age_sec: int = 60,
        interval_sec: int = 30,
        mem_limit_bytes: Optional[int] = None,
    ):
        self._sink = sink
        self._watchdog = watchdog
        self._runner = execution_runner
        self._db = db_manager
        self._redis = redis_client
        self._data_dir = Path(data_dir)
        self._long_task_age_sec = long_task_age_sec
        self._interval = interval_sec
        self._mem_limit = mem_limit_bytes  # None = 用 ulimit fallback

        self._proc = psutil.Process(os.getpid())
        # 第一次调 cpu_percent 是 prime,得到 0;后面才有真实读数
        self._proc.cpu_percent(interval=None)

        self._task: Optional[asyncio.Task] = None
        # 暴露最近一次 snapshot 给 /admin/runtime
        self._latest: dict = {}

        # FD ulimit (POSIX);Windows 上 RLIMIT_NOFILE 不存在
        try:
            self._fd_soft_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        except (ValueError, OSError):
            self._fd_soft_limit = 0

    def latest_snapshot(self) -> dict:
        return dict(self._latest)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop(), name="obs-sampler")
        logger.info(
            f"RuntimeSampler started (interval={self._interval}s, "
            f"long_task_age={self._long_task_age_sec}s)"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None
        logger.info("RuntimeSampler stopped")

    # ── 内部实现 ─────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 不能让 sampler 死掉;打 WARN 便于排查
                logger.exception("RuntimeSampler tick failed; continuing")
            await asyncio.sleep(self._interval)

    async def sample_once(self) -> dict:
        """采一次并写 jsonl。返回采集的 snapshot(便于测试与 /admin/runtime 复用)。"""
        snapshot: dict = {
            "ts": utc_now().isoformat(),
        }

        # ── loop lag(来自 watchdog 滚动窗口) ──
        snapshot["loop_lag_ms"] = (
            self._watchdog.snapshot() if self._watchdog else {}
        )

        # ── in-flight / tasks ──
        in_flight, long_running = self._collect_runner_state()
        snapshot["in_flight"] = in_flight
        snapshot["tasks_long_running"] = long_running
        try:
            snapshot["tasks_total"] = len(asyncio.all_tasks())
        except RuntimeError:
            snapshot["tasks_total"] = 0

        # ── DB pool ──
        snapshot["db_pool"] = self._collect_db_pool()

        # ── Redis ──
        snapshot["redis"] = await self._collect_redis()

        # ── Process ──
        process_stats = self._collect_process()
        snapshot["process"] = process_stats

        # ── data dir 大小 ──
        # rglob 在卷大时可达秒级,丢线程池避免 observer 自己制造 loop_lag。
        snapshot["data_dir_mb"] = await asyncio.to_thread(self._data_dir_size_mb)

        # 写入 jsonl(swallow on error,JsonlSink 内已防御)
        self._sink.write(snapshot)
        self._latest = snapshot

        # 高水位告警
        self._check_thresholds(snapshot)

        return snapshot

    def _collect_runner_state(self) -> tuple[int, int]:
        """返回 (in_flight, long_running) — long_running 是超 age 阈值的任务数。

        long_running_count 是 ExecutionRunner 上的可选方法;不存在时(如测试
        fake)优雅退化为 0,而不是让 sampler 整个采样失败。
        """
        if self._runner is None:
            return 0, 0
        try:
            tasks = getattr(self._runner, "_tasks", {})
            in_flight = len(tasks)
        except Exception:
            return 0, 0

        try:
            long_running_fn = getattr(self._runner, "long_running_count", None)
            if callable(long_running_fn):
                long_running = int(long_running_fn(self._long_task_age_sec))
            else:
                long_running = 0
        except Exception:
            long_running = 0
        return in_flight, long_running

    def _collect_db_pool(self) -> dict:
        """从 SQLAlchemy 引擎的 QueuePool 拉计数(SQLite StaticPool 没这些字段)。"""
        if self._db is None:
            return {}
        try:
            engine = getattr(self._db, "_engine", None)
            if engine is None:
                return {}
            pool = engine.sync_engine.pool
            # QueuePool 有 size/checkedout/overflow/checkedin。StaticPool 等没有。
            getter = lambda name: getattr(pool, name, lambda: None)
            return {
                "in_use": _safe_int(getter("checkedout")()),
                "size": _safe_int(getter("size")()),
                "overflow": _safe_int(getter("overflow")()),
            }
        except Exception:
            return {}

    async def _collect_redis(self) -> dict:
        if self._redis is None:
            return {}
        try:
            info = await self._redis.info("memory")
            used = int(info.get("used_memory", 0))
            max_mem = int(info.get("maxmemory", 0))
            return {
                "used_mb": round(used / 1024 / 1024, 1),
                "maxmemory_mb": round(max_mem / 1024 / 1024, 1) if max_mem else 0,
            }
        except Exception:
            return {}

    def _collect_process(self) -> dict:
        try:
            mem = self._proc.memory_info()
            cpu = self._proc.cpu_percent(interval=None)
            try:
                fds = self._proc.num_fds()
            except (AttributeError, psutil.AccessDenied):
                fds = 0
            return {
                "rss_mb": round(mem.rss / 1024 / 1024, 1),
                "cpu_pct": round(cpu, 1),
                "open_fds": fds,
            }
        except Exception:
            return {}

    def _data_dir_size_mb(self) -> float:
        """recursive du-style 求 data/ 目录占用;不存在则 0。"""
        if not self._data_dir.exists():
            return 0.0
        try:
            total = 0
            for entry in self._data_dir.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        continue
            return round(total / 1024 / 1024, 1)
        except Exception:
            return 0.0

    def _check_thresholds(self, snapshot: dict) -> None:
        """逼近上限打 WARN(loud failure)。"""
        process = snapshot.get("process", {})
        rss_mb = process.get("rss_mb", 0)
        open_fds = process.get("open_fds", 0)

        # RSS vs mem_limit(若未配置,跳过 — 我们不假设 host 总内存代表 backend 上限)
        if self._mem_limit:
            limit_mb = self._mem_limit / 1024 / 1024
            if rss_mb > limit_mb * self._RSS_WARN_RATIO:
                logger.warning(
                    f"Process RSS {rss_mb:.0f}MB exceeds "
                    f"{self._RSS_WARN_RATIO*100:.0f}% of mem_limit {limit_mb:.0f}MB"
                )

        # FD vs RLIMIT_NOFILE
        if self._fd_soft_limit and open_fds > self._fd_soft_limit * self._FD_WARN_RATIO:
            logger.warning(
                f"Open FDs {open_fds} exceed "
                f"{self._FD_WARN_RATIO*100:.0f}% of ulimit {self._fd_soft_limit}"
            )

        # DB pool waiters > 0(SQLAlchemy 的 QueuePool 没有直接 waiters 计数 — 用
        # overflow 是否在涨判断;真有 waiters 通常表现为 connection acquisition 慢。
        # 留作 follow-up:若需精确,可在 acquire 处加 instrumentation)
        db_pool = snapshot.get("db_pool", {})
        overflow = db_pool.get("overflow", 0)
        size = db_pool.get("size", 0)
        if size and overflow > 0:
            # overflow > 0 说明 pool_size 已饱,正在用 max_overflow 兜底 — 高水位信号
            logger.warning(
                f"DB pool saturated: in_use={db_pool.get('in_use', 0)}, "
                f"size={size}, overflow={overflow}"
            )

        # Redis used vs maxmemory
        redis = snapshot.get("redis", {})
        used_mb = redis.get("used_mb", 0)
        max_mb = redis.get("maxmemory_mb", 0)
        if max_mb and used_mb > max_mb * self._REDIS_USED_WARN_RATIO:
            logger.warning(
                f"Redis used_memory {used_mb:.0f}MB exceeds "
                f"{self._REDIS_USED_WARN_RATIO*100:.0f}% of maxmemory {max_mb:.0f}MB"
            )


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


# cgroup v2 把内存上限放这里,unlimited 时写 "max"。
_CGROUP_V2_MEMORY_MAX = "/sys/fs/cgroup/memory.max"
# cgroup v1 hier;unlimited 时是一个巨大数字(常见 9223372036854771712)。
_CGROUP_V1_MEMORY_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
# 超过这个值视为 "未设上限"(避免把 ~8 EiB 当成真实限额导致 80% 阈值永远过不去)
_CGROUP_UNLIMITED_SENTINEL = 1 << 60


def resolve_mem_limit_bytes(explicit_mb: int = 0) -> Optional[int]:
    """决定 RSS 高水位告警的上界。

    优先级:
        1. explicit_mb > 0 → 直接用(env override / docker-compose mem_limit 镜像)
        2. cgroup v2 (`/sys/fs/cgroup/memory.max`,值为 "max" 时跳过)
        3. cgroup v1 (`/sys/fs/cgroup/memory/memory.limit_in_bytes`)
        4. 都读不到 / 都 unlimited → None(sampler 此时不告警,保持现状)

    任何 IO / 解析错误都返回 None — observer 不能挂应用。
    """
    if explicit_mb > 0:
        return int(explicit_mb) * 1024 * 1024

    try:
        with open(_CGROUP_V2_MEMORY_MAX, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v and v != "max":
            n = int(v)
            if 0 < n < _CGROUP_UNLIMITED_SENTINEL:
                return n
    except (OSError, ValueError):
        pass

    try:
        with open(_CGROUP_V1_MEMORY_LIMIT, "r", encoding="utf-8") as f:
            n = int(f.read().strip())
        if 0 < n < _CGROUP_UNLIMITED_SENTINEL:
            return n
    except (OSError, ValueError):
        pass

    return None
