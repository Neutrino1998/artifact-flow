"""
HeartbeatWriter — 舰队心跳注册表(Phase C 决策 4)

不是新的常驻循环:RuntimeSampler 每 OBS_SAMPLE_INTERVAL_SEC 已经产出我们要的整份
运行时快照,心跳只是在每次采样末尾把其中一份子集**多写一份到 Redis**,让管理端
`GET /admin/instances` 能 scan 出全舰队而不必逐个 LB 路由去问 /admin/runtime。

Key 形状 `{<prefix>:instance:<id>}` —— hash-tag 把每个实例钉在各自 slot,distinct
实例天然散列到不同 slot,读侧 scan + 逐 key pipelined GET fan-out(镜像
`RedisRuntimeStore.list_active_executions`),全程无跨 slot 多 key 操作,standalone /
Sentinel / Cluster 三种形态同一份代码都正确。

写用单条 `SET key json EX ttl`(不是 hash+EXPIRE 两条):单命令原子,无「HSET 成功
但 EXPIRE 前崩」的裸 key 竞态,读侧一次 GET + json.loads 拿全量,最简。

**双时间轴红/黄 by-construction**(对 plan「TTL ~90s」草图的修正):TTL 放长
(OBS_HEARTBEAT_TTL_SEC,默认 300s),颜色由 payload 里的 `ts` 新鲜度在读侧判 ——
心跳写循环是 asyncio task,loop 卡死 → 不再写 → `ts` 停更但 key 还在 TTL 窗口内
→ 面板「红」(wedge 在册可见),给 autoheal 留重启窗口;key 真过期(>TTL)才从
列表消失(死透且已收殓)。若 TTL 只有 ~90s,wedge 实例的 key 直接过期消失,面板
根本没机会显红 —— 与 Phase B 那三个 LB bug 同性质的构造性修正。

observer 不能拖累 observee:写失败一律吞(debug 一行),绝不冒泡回 sampler tick。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from utils.instance import INSTANCE_ID
from utils.logger import get_logger
from utils.time import utc_now

logger = get_logger("ArtifactFlow")


class HeartbeatWriter:
    """把 sampler 快照子集 + 进程元数据写进 `{prefix:instance:<id>}`(TTL)。"""

    def __init__(
        self,
        *,
        redis_client: Any,
        key_prefix: str,
        ttl_sec: int,
        error_counter: Any = None,
        watchdog: Any = None,
        autoheal_marker_path: str = "",
    ):
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl = int(ttl_sec)
        self._error_counter = error_counter
        self._watchdog = watchdog
        self._marker_path = autoheal_marker_path

        # 进程起点:面板显示「本行连续但 started_at 变新」= 被(autoheal)重启过。
        self._started_at = utc_now().isoformat()
        # 镜像版本:compose 把 AF_VERSION 注进 backend env(见 docker-compose.*.yml)。
        # 非 ARTIFACTFLOW_ 前缀,不过 config;直读 env,缺省 "dev"。
        self._version = os.environ.get("AF_VERSION", "dev")

    # ── Key 形状(单一真相源,读侧 admin_runtime 引用同名 staticmethod) ──

    @staticmethod
    def instance_key(prefix: str, instance_id: str) -> str:
        return f"{{{prefix}:instance:{instance_id}}}"

    @staticmethod
    def scan_pattern(prefix: str) -> str:
        return f"{{{prefix}:instance:*}}"

    @staticmethod
    def instance_id_from_key(key: str) -> str:
        """从 `{prefix:instance:<id>}` 反解出 <id>(读侧兜底用,payload 内也带)。"""
        # tag 内容在首 `{` 与首 `}` 之间;<id> 在最后一个 `:` 之后。
        inner = key[key.index("{") + 1 : key.index("}")]
        return inner.rsplit(":", 1)[-1]

    # ── 写 ──

    async def write(self, snapshot: dict) -> None:
        """由 sampler 每 tick 调用。redis 为 None(单机 InMemory)时 no-op。"""
        if self._redis is None:
            return
        try:
            payload = self._build_payload(snapshot)
            key = self.instance_key(self._prefix, INSTANCE_ID)
            await self._redis.set(key, json.dumps(payload), ex=self._ttl)
        except Exception:
            # observer 不能挂应用;debug 级别(多副本高频路径,warn 会刷屏)。
            logger.debug("Heartbeat write failed; skipping this tick", exc_info=True)

    def build_local_payload(self, snapshot: dict) -> dict:
        """读侧在单机(无 Redis)形态下,用本机最近 snapshot 造出与注册表同构的一行。"""
        return self._build_payload(snapshot)

    def _build_payload(self, snapshot: dict) -> dict:
        ec = self._error_counter
        wd = self._watchdog
        return {
            "instance_id": INSTANCE_ID,
            "version": self._version,
            "started_at": self._started_at,
            # sampler 快照子集(面板要展示/判色的字段)
            "ts": snapshot.get("ts"),
            "loop_lag_ms": snapshot.get("loop_lag_ms", {}),
            "in_flight": snapshot.get("in_flight", 0),
            "tasks_long_running": snapshot.get("tasks_long_running", 0),
            "process": snapshot.get("process", {}),
            "db_pool": snapshot.get("db_pool", {}),
            "redis": snapshot.get("redis", {}),
            "data_dir_mb": snapshot.get("data_dir_mb", 0),
            # 进程内计数/摘要
            "error_count": getattr(ec, "count", 0) if ec else 0,
            "last_error_ts": getattr(ec, "last_ts", None) if ec else None,
            "last_wedge": wd.last_wedge() if wd is not None else None,
            # 宿主 autoheal marker 代报(见 _read_autoheal_marker)
            "last_autoheal": self._read_autoheal_marker(),
        }

    # ── autoheal marker 代报 ──

    def _read_autoheal_marker(self) -> Optional[dict]:
        """读宿主 autoheal 追加型 marker,返回本实例最近一次被重启的摘要。

        marker 是 autoheal.sh 追加的 JSONL,每行 {ts, instance_id, container, reason};
        目录只读挂进 backend。这里过滤 instance_id == 本机 INSTANCE_ID(docker restart
        保容器身份 → hostname 不变 → 心跳同一行连续),取最近一条 + 累计次数。

        宿主脚本不直连 Redis(保十行可审计),归因经这个文件中转、本机容器代报。
        文件不存在 / 未配置 → None(面板不显示「曾被重启」)。任何 IO/解析错误吞成 None。
        """
        if not self._marker_path:
            return None
        try:
            path = Path(self._marker_path)
            if not path.exists():
                return None
            last: Optional[dict] = None
            count = 0
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("instance_id") != INSTANCE_ID:
                        continue
                    count += 1
                    last = rec
            if last is None:
                return None
            return {
                "ts": last.get("ts"),
                "reason": last.get("reason"),
                "count": count,
            }
        except Exception:
            return None
