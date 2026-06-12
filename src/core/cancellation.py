"""
协作式 cancel 的可打断 await — run_cancellable

背景：协作式 cancel（store.request_cancel → hooks.check_cancelled）原本只在
engine loop 的"缝隙"处被消费（loop 顶部 / 每个工具执行前 / LLM 流式 chunk 间 /
permission 等待）。两个长 await 是盲窗：工具执行本体（bash 数百秒、HttpTool
per-MD timeout 运维可任意设）和 compaction LLM 调用（COMPACTION_TIMEOUT）——
cancel 落在其中时延迟 = 该 await 自己的内部超时。

本模块把"await 期间轮询 cancel flag"收成一个引擎侧原语：被裹的 awaitable 跑在
子 task 里，调用方按 poll_interval 轮询 flag，命中即 task.cancel() 子 task 并抛
CooperativeCancelled。工具作者零新义务 —— cancel-safety 的契约不是新的：
EXECUTION_TIMEOUT 的 asyncio.timeout 本来就会在任意 await 中间 cancel 整个
engine task，工具早已被要求 cancel-safe（见 docs/architecture/execution-lifecycle.md）。

与外部 cancel（lease fencing / EXECUTION_TIMEOUT deadline）的辨析：那些路径
cancel 的是**调用方所在的 task**，会在 asyncio.wait / flag 轮询处以
CancelledError 抛进来 —— 此处转发给子 task 后原样 re-raise（绝不吞、绝不转换），
两条路径不混淆（与 controller.py 对 asyncio.timeout 的同款辨析一致）。

GIL 警告（同 EXECUTION_TIMEOUT）：task.cancel() 是协作式的，打不断钉住 GIL 的
同步 CPU 工具 —— 工具作者仍自己兜 wall-clock（CLAUDE.md「Tool authors own
CPU-cost discipline」）。
"""

import asyncio
from typing import Any, Awaitable, Callable

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


class CooperativeCancelled(Exception):
    """协作式 cancel flag 在 await 期间被置位，在飞 awaitable 已被 cancel。

    Exception（非 BaseException）子类：调用方必须在 `except Exception` 兜底
    **之前**显式接住它路由到 CANCELLED 终态，否则会被当成 ERROR —— 测试
    test_engine_execution.py 对此有回归。
    """


async def run_cancellable(
    awaitable: Awaitable[Any],
    is_cancelled: Callable[[], Awaitable[bool]],
    poll_interval: float,
) -> Any:
    """在子 task 中 await，期间按 poll_interval 轮询协作式 cancel flag。

    Args:
        awaitable: 要执行的 coroutine（在此被包成 task）
        is_cancelled: 零参 async 谓词（调用方预绑定 message_id），True = 已请求取消
        poll_interval: 轮询间隔秒。由调用方显式传入（通常 config.CANCEL_CHECK_INTERVAL）
            而非本模块自读 config —— 测试桩 patch 的是调用方模块的 config。

    Returns:
        awaitable 的结果。

    Raises:
        CooperativeCancelled: flag 命中；子 task 已被 cancel 并 await 收尾。
        asyncio.CancelledError: 调用方 task 被外部 cancel（fencing / 引擎超时）——
            转发给子 task 后原样 re-raise。
        其余异常: awaitable 自身的异常原样穿透（子 task 已结束，无需清理）。
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_interval)
            if done:
                return task.result()  # 工具自身异常在此原样抛出
            if await is_cancelled():
                raise CooperativeCancelled()
    except BaseException:
        # 三类走到这里：CooperativeCancelled（自己抛的）、外部 CancelledError
        # （asyncio.wait 或 is_cancelled 处被打断 —— asyncio.wait 被 cancel 不会
        # 自动 cancel 它等的 task，不转发就泄漏在飞工具）、is_cancelled 自身异常
        # （如 Redis 故障）。统一兜底：子 task 未完成则 cancel 并 await 收尾，
        # 再原样 re-raise。
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as cleanup_err:
                # 子 task 在取消收尾中抛了别的 —— 只记日志，不掩盖原始控制流
                logger.warning(
                    f"run_cancellable: awaitable raised during cancel cleanup: {cleanup_err}"
                )
        raise
