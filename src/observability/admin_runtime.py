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

from typing import Any

from fastapi import APIRouter, Depends

from api.dependencies import get_runtime_store, get_execution_runner, require_admin
from utils.time import utc_now
from api.services.auth import TokenPayload

router = APIRouter()


# 单例 RuntimeSampler 由 lifespan 注入(避免硬循环依赖 dependencies.py)
_sampler: Any = None


def set_sampler(sampler: Any) -> None:
    """由 lifespan 启动时注入;若未注入,端点返回 sampler 字段为空字典。"""
    global _sampler
    _sampler = sampler


def get_sampler() -> Any:
    return _sampler


@router.get("/runtime")
async def get_runtime(
    _admin: TokenPayload = Depends(require_admin),
):
    """
    实时水位 + 活跃任务诊断快照。

    Response:
        {
            "ts": ISO8601,
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
        "sampler": snapshot,
        "active_conversations": active_conv_ids,
        "active_tasks": runner.active_task_count,
    }
