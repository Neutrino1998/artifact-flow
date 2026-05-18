"""
FastAPI 应用入口

创建 FastAPI 应用实例，配置中间件和路由。
"""

import asyncio
import faulthandler
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse
from sqlalchemy import text

from config import config, validate_config
from api.dependencies import (
    init_globals, close_globals,
    get_db_manager, get_redis_client,
    get_execution_runner,
)
from api.routers import admin, admin_users, auth, chat, artifacts, departments, stream
from observability import (
    LoopLagWatchdog, DeadmanSwitch, RuntimeSampler, JsonlSink,
    resolve_mem_limit_bytes,
)
from observability import admin_runtime
from utils.doc_converter import DocConverter
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# Observability 组件句柄（生命周期跨 lifespan;在 startup 创建,shutdown 关闭)
_watchdog: Optional[LoopLagWatchdog] = None
_deadman: Optional[DeadmanSwitch] = None
_sampler: Optional[RuntimeSampler] = None
_loop_lag_sink: Optional[JsonlSink] = None
_metrics_sink: Optional[JsonlSink] = None


def _enable_faulthandler() -> None:
    """启用 faulthandler:致命信号 stderr dump + SIGUSR1 手动 dump。

    deadman switch 用的 dump_traceback_later 走 C 线程,与本函数注册的两个
    handler 互不冲突,但都依赖 faulthandler.enable() 已生效。
    """
    try:
        faulthandler.enable(file=sys.stderr)
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, chain=False)
        logger.info("faulthandler enabled (SIGSEGV-class crash + SIGUSR1 manual dump)")
    except Exception:
        logger.exception("Failed to enable faulthandler; manual dump unavailable")


def _start_observability(loop: asyncio.AbstractEventLoop) -> None:
    """启动 observability 三件套(watchdog / deadman / sampler)。

    顺序:
        1. faulthandler.enable() — 必须最早,deadman 依赖
        2. watchdog 线程 — Python 线程,独立于 asyncio loop
        3. deadman 心跳 task — 在 loop 上
        4. sampler task — 在 loop 上,引用 watchdog snapshot
    """
    global _watchdog, _deadman, _sampler, _loop_lag_sink, _metrics_sink

    _enable_faulthandler()

    # loop-lag.jsonl sink
    _loop_lag_sink = JsonlSink(
        Path(config.OBS_LOOP_LAG_LOG_PATH),
        max_mb=config.OBS_JSONL_MAX_MB,
        backups=config.OBS_JSONL_BACKUP_COUNT,
    )
    _watchdog = LoopLagWatchdog(
        loop=loop,
        sink=_loop_lag_sink,
        warn_ms=config.LOOP_LAG_WARN_MS,
    )
    _watchdog.start()

    _deadman = DeadmanSwitch(timeout_ms=config.WATCHDOG_DEADMAN_TIMEOUT_MS)
    _deadman.start()

    _metrics_sink = JsonlSink(
        Path(config.OBS_METRICS_LOG_PATH),
        max_mb=config.OBS_JSONL_MAX_MB,
        backups=config.OBS_JSONL_BACKUP_COUNT,
    )
    # mem_limit:env override > cgroup v2 > cgroup v1 > None。读不到时
    # sampler 不告警(保持现状),不再让 RSS 阈值永远沉默。
    mem_limit_bytes = resolve_mem_limit_bytes(config.OBS_MEM_LIMIT_MB)
    if mem_limit_bytes:
        logger.info(
            f"Observability mem_limit resolved: "
            f"{mem_limit_bytes // (1024 * 1024)} MB "
            f"(source={'env' if config.OBS_MEM_LIMIT_MB else 'cgroup'})"
        )
    else:
        logger.info(
            "Observability mem_limit unset (no env override and no readable cgroup) — "
            "RSS high-water WARN disabled"
        )
    _sampler = RuntimeSampler(
        sink=_metrics_sink,
        watchdog=_watchdog,
        execution_runner=get_execution_runner(),
        db_manager=get_db_manager(),
        redis_client=get_redis_client(),
        long_task_age_sec=config.OBS_LONG_TASK_AGE_SEC,
        interval_sec=config.OBS_SAMPLE_INTERVAL_SEC,
        mem_limit_bytes=mem_limit_bytes,
    )
    _sampler.start()
    admin_runtime.set_sampler(_sampler)


async def _stop_observability() -> None:
    """对称收尾:sampler → deadman → watchdog → sinks。"""
    global _watchdog, _deadman, _sampler, _loop_lag_sink, _metrics_sink

    if _sampler is not None:
        await _sampler.stop()
        _sampler = None
        admin_runtime.set_sampler(None)
    if _deadman is not None:
        await _deadman.stop()
        _deadman = None
    if _watchdog is not None:
        _watchdog.stop()
        _watchdog = None
    if _metrics_sink is not None:
        _metrics_sink.close()
        _metrics_sink = None
    if _loop_lag_sink is not None:
        _loop_lag_sink.close()
        _loop_lag_sink = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时：初始化全局单例（数据库、StreamTransport、ExecutionRunner）+ 观测组件
    关闭时：对称清理
    """
    # 启动
    logger.info("Starting ArtifactFlow API...")
    validate_config()
    DocConverter.check_pandoc()
    await init_globals()

    # Sync logger debug level from API config (single source of truth)
    from utils.logger import set_global_debug
    set_global_debug(config.DEBUG)

    # 观测组件(在 init_globals 之后,依赖 ExecutionRunner / DatabaseManager / Redis 单例)
    try:
        _start_observability(asyncio.get_running_loop())
    except Exception:
        # 观测层失败不挂应用启动 — 但留 ERROR 便于发现
        logger.exception("Observability bootstrap failed; continuing without it")

    logger.info("ArtifactFlow API started successfully")

    yield

    # 关闭
    logger.info("Shutting down ArtifactFlow API...")
    try:
        await _stop_observability()
    except Exception:
        logger.exception("Observability shutdown failed; continuing")
    await close_globals()
    logger.info("ArtifactFlow API shutdown complete")


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用

    Returns:
        配置好的 FastAPI 应用实例
    """
    app = FastAPI(
        title="ArtifactFlow API",
        description="Multi-agent system API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if config.DEBUG else None,
        redoc_url="/redoc" if config.DEBUG else None,
    )

    # 配置 CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        allow_methods=config.CORS_ALLOW_METHODS,
        allow_headers=config.CORS_ALLOW_HEADERS,
    )

    # 注册路由
    app.include_router(
        auth.router,
        prefix="/api/v1/auth",
        tags=["auth"]
    )
    app.include_router(
        chat.router,
        prefix="/api/v1/chat",
        tags=["chat"]
    )
    app.include_router(
        artifacts.router,
        prefix="/api/v1/artifacts",
        tags=["artifacts"]
    )
    app.include_router(
        stream.router,
        prefix="/api/v1/stream",
        tags=["stream"]
    )
    app.include_router(
        admin.router,
        prefix="/api/v1/admin",
        tags=["admin"]
    )
    app.include_router(
        admin_users.router,
        prefix="/api/v1/admin",
        tags=["admin"]
    )
    app.include_router(
        admin_runtime.router,
        prefix="/api/v1/admin",
        tags=["admin"]
    )
    app.include_router(
        departments.router,
        prefix="/api/v1/departments",
        tags=["departments"]
    )

    # 健康检查端点
    @app.get("/health/live")
    async def liveness():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness():
        checks: dict = {}
        ok = True

        # DB check
        try:
            db = get_db_manager()
            async with db.session() as session:
                await session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception:
            logger.exception("Readiness: DB check failed")
            checks["db"] = "error"
            ok = False

        # Redis check (optional)
        redis = get_redis_client()
        if redis is not None:
            try:
                await redis.ping()
                checks["redis"] = "ok"
            except Exception:
                logger.exception("Readiness: Redis check failed")
                checks["redis"] = "error"
                ok = False

        status_code = 200 if ok else 503
        return JSONResponse(
            content={"status": "ok" if ok else "error", **checks},
            status_code=status_code,
        )

    return app


# 创建应用实例
app = create_app()
