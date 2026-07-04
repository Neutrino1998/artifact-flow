"""
/admin/runtime — 半活状态诊断端点

定位:**服务还活但变慢 / 资源逼近上限**。pool 即将耗尽、Redis 接近 maxmemory、
有长跑任务、loop_lag 在抬升但还能调度。这类 "走慢了但还回得来" 状态下用它看
实时水位。

**不是硬 wedge 第一入口** — 本身就是 FastAPI 协程端点,事件循环卡死它跟
/health/live 一样无响应(本次 2026-05-14 事故已证)。硬 wedge 的第一入口是
DeadmanSwitch 的 stderr dump + docker healthcheck 状态 + `kill -USR1 <pid>`
手动 dump,全在 Python 解释器之外。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends

from config import config
from api.dependencies import (
    get_runtime_store,
    get_execution_runner,
    get_redis_client,
    require_admin,
)
from observability.heartbeat import HeartbeatWriter
from utils.instance import INSTANCE_ID
from utils.logger import get_logger
from utils.time import utc_now
from api.services.auth import TokenPayload

logger = get_logger("ArtifactFlow")

router = APIRouter()


# 单例 RuntimeSampler / HeartbeatWriter 由 lifespan 注入(避免硬循环依赖 dependencies.py)
_sampler: Any = None
_heartbeat: Any = None


def set_sampler(sampler: Any) -> None:
    """由 lifespan 启动时注入;若未注入,端点返回 sampler 字段为空字典。"""
    global _sampler
    _sampler = sampler


def get_sampler() -> Any:
    return _sampler


def set_heartbeat(heartbeat: Any) -> None:
    """由 lifespan 注入 HeartbeatWriter;/instances 读侧用其 key 形状 + 本机 payload 构造。"""
    global _heartbeat
    _heartbeat = heartbeat


def get_heartbeat() -> Any:
    return _heartbeat


@router.get("/runtime")
async def get_runtime(
    _admin: TokenPayload = Depends(require_admin),
):
    """
    实时水位 + 活跃任务诊断快照。

    Response:
        {
            "ts": ISO8601,
            "instance_id": str,   # 本次应答实例 — sampler/active_tasks 是进程本地视图,
                                  # 多副本经 LB 随机路由时读数跳变由此可解释
            "sampler": {<sampler.latest_snapshot 结构,见 sampler.py 文档>},
            "active_conversations": [conv_id, ...],
            "active_tasks": int,
        }
    """
    sampler = get_sampler()
    runner = get_execution_runner()
    store = get_runtime_store()

    try:
        active_conv_ids = await store.list_active_conversations()
    except Exception:
        active_conv_ids = []

    snapshot = sampler.latest_snapshot() if sampler is not None else {}

    return {
        "ts": utc_now().isoformat(),
        "instance_id": INSTANCE_ID,
        "sampler": snapshot,
        "active_conversations": active_conv_ids,
        "active_tasks": runner.active_task_count,
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    """解析 payload 里的 naive-UTC ISO 字符串;失败返回 None。"""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _compute_status(payload: dict, now: datetime) -> str:
    """读侧判色(green / yellow / red),不落 Redis —— 阈值可随时调不需回填。

    - red:ts 缺失或陈旧(≥ STALE_SEC)。心跳是 asyncio task,loop wedge → ts 停更
      → 陈旧 → 红(key 仍在册,给 autoheal 留窗口;key 真过期则整条从 scan 消失)。
    - yellow:活着但有异常信号之一 —— loop_lag 近一分钟峰值超 warn 阈 / 窗口内出过
      ERROR / watchdog 抓到过 wedge / 近期被 autoheal 重启过。
    - green:新鲜且无上述信号。
    """
    ts = _parse_ts(payload.get("ts"))
    if ts is None or (now - ts).total_seconds() >= config.OBS_HEARTBEAT_STALE_SEC:
        return "red"

    # loop_lag 近一分钟峰值
    loop_lag = payload.get("loop_lag_ms") or {}
    if float(loop_lag.get("max_1m_ms", 0) or 0) >= config.LOOP_LAG_WARN_MS:
        return "yellow"

    # 窗口内出过 ERROR
    last_error = _parse_ts(payload.get("last_error_ts"))
    if last_error is not None and (now - last_error).total_seconds() <= config.OBS_ERROR_WINDOW_SEC:
        return "yellow"

    # watchdog 抓到过 wedge(进程生命周期内曾发生即黄,与 plan「是否抓到过」一致)
    if payload.get("last_wedge"):
        return "yellow"

    # 近期被 autoheal 重启过
    autoheal = payload.get("last_autoheal") or {}
    healed_ts = _parse_ts(autoheal.get("ts"))
    if healed_ts is not None and (now - healed_ts).total_seconds() <= config.OBS_ERROR_WINDOW_SEC:
        return "yellow"

    return "green"


@router.get("/instances")
async def list_instances(
    _admin: TokenPayload = Depends(require_admin),
):
    """
    舰队实例面板数据源(Phase C 决策 4)。

    多副本(Redis):scan `{prefix:instance:*}` + pipelined GET fan-out(镜像
    RedisRuntimeStore.list_active_executions,Cluster-safe —— 无跨 slot 多 key 操作),
    每条心跳 payload 读侧附一个 status(green/yellow/red)。
    单机(InMemory,无 Redis):没有注册表可 scan,用本机最近 snapshot 造出唯一一行。

    Response:
        {
            "ts": ISO8601,
            "instance_id": str,        # 本次应答实例
            "shared": bool,            # True=Redis 舰队视图;False=单机本地视图
            "instances": [ {<心跳 payload>, "status": "green|yellow|red"}, ... ],
        }
    """
    now = utc_now()
    redis = get_redis_client()
    heartbeat = get_heartbeat()

    instances: list[dict] = []

    if redis is None:
        # 单机形态:无注册表,用本机 sampler snapshot 造一行(与心跳 payload 同构)。
        sampler = get_sampler()
        snapshot = sampler.latest_snapshot() if sampler is not None else {}
        if heartbeat is not None:
            payload = heartbeat.build_local_payload(snapshot)
        else:
            payload = {"instance_id": INSTANCE_ID, "ts": snapshot.get("ts")}
        payload["status"] = _compute_status(payload, now)
        instances.append(payload)
        return {
            "ts": now.isoformat(),
            "instance_id": INSTANCE_ID,
            "shared": False,
            "instances": instances,
        }

    # 多副本形态:scan + pipelined GET(镜像 list_active_executions)。
    prefix = config.REDIS_KEY_PREFIX
    pattern = HeartbeatWriter.scan_pattern(prefix)
    try:
        keys: list[str] = []
        async for key in redis.scan_iter(match=pattern, count=100):
            keys.append(key if isinstance(key, str) else key.decode())
        if keys:
            pipe = redis.pipeline(transaction=False)
            for k in keys:
                pipe.get(k)
            values = await pipe.execute()  # command order
            for k, raw in zip(keys, values):
                if raw is None:
                    continue  # TTL 过期于 scan 与 GET 之间 → 自然掉出(= 已死)
                try:
                    payload = json.loads(raw if isinstance(raw, str) else raw.decode())
                except (json.JSONDecodeError, TypeError):
                    # 坏行不该拖垮整表:退化成一条只带 id 的红行,便于发现。
                    payload = {"instance_id": HeartbeatWriter.instance_id_from_key(k), "ts": None}
                payload["status"] = _compute_status(payload, now)
                instances.append(payload)
    except Exception as e:
        # scan/pipeline 失败是**已处理的降级**(返空表 200,非 5xx),按 CLAUDE.md 日志
        # 规矩属 expected/handled → warning 且不带栈(栈无 useful 信息)。面板 10s 轮询,
        # 带 exc_info 会让一次 Redis 抖动每 10s 每个管理员刷一坨栈。
        logger.warning(f"Failed to scan instance heartbeats for /admin/instances: {e}")

    # 新→旧稳定排序:先按 status 严重度(red>yellow>green),再按 instance_id。
    severity = {"red": 0, "yellow": 1, "green": 2}
    instances.sort(key=lambda p: (severity.get(p.get("status"), 3), p.get("instance_id") or ""))

    return {
        "ts": now.isoformat(),
        "instance_id": INSTANCE_ID,
        "shared": True,
        "instances": instances,
    }
