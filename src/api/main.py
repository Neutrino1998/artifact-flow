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
from api.middleware import RequestContextMiddleware
from api.routers import admin, admin_tools, admin_users, auth, chat, artifacts, departments, meta, skills, stream
from observability import (
    LoopLagWatchdog, DeadmanSwitch, RuntimeSampler, JsonlSink,
    resolve_mem_limit_bytes,
)
from observability import admin_runtime
from utils.logger import get_logger, get_request_id

logger = get_logger("ArtifactFlow")


# Observability 组件句柄（生命周期跨 lifespan;在 startup 创建,shutdown 关闭)
_watchdog: Optional[LoopLagWatchdog] = None
_deadman: Optional[DeadmanSwitch] = None
_sampler: Optional[RuntimeSampler] = None
_loop_lag_sink: Optional[JsonlSink] = None
_metrics_sink: Optional[JsonlSink] = None

# lease-anchored 沙盒孤儿回收器(C-reap);生命周期跨 lifespan
_sandbox_reaper = None  # type: ignore[var-annotated]


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
        mirror_stdout=config.OBS_STDOUT_MIRROR,
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
        mirror_stdout=config.OBS_STDOUT_MIRROR,
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


def _should_start_reaper(
    *, enabled: bool, store_is_shared: bool, allow_local_store: bool
) -> tuple[bool, str]:
    """沙盒 reaper 启停判定 → (start?, reason)。提纯成函数好单测。

    跨进程安全要求**共享** liveness 源(Redis):InMemory 是进程本地,多副本下每个进程
    把兄弟的活沙盒看成无 lease 孤儿 → 误删(破坏性,非仅降级 —— lease/stream 在 InMemory
    多 worker 下本就坏,但只是"找不到";reaper 把同一误配升级成"删活资源")。故 InMemory
    下默认不起,需操作者显式 affirm 单进程(SANDBOX_REAP_ALLOW_LOCAL_STORE)。
    """
    if not enabled:
        return False, "SANDBOX_REAP_ENABLED=false"
    if store_is_shared:
        return True, "shared store (Redis)"
    if allow_local_store:
        return True, "process-local store, operator-affirmed single-worker"
    return False, (
        "process-local (InMemory) store — disabled to avoid cross-deleting sibling "
        "sandboxes if multiple workers/replicas run without Redis; set "
        "SANDBOX_REAP_ALLOW_LOCAL_STORE=true for single-worker InMemory, or use Redis"
    )


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

    # 沙盒孤儿回收器(在 init_globals 之后:依赖 ExecutionRunner.store 取活跃集)。
    # 启动失败不挂应用 —— 它是兜底,缺它只是 SIGKILL 路径少一层防护。
    global _sandbox_reaper
    store = get_execution_runner().store
    store_is_shared = getattr(store, "is_shared", False)
    start_reaper, reason = _should_start_reaper(
        enabled=config.SANDBOX_REAP_ENABLED,
        store_is_shared=store_is_shared,
        allow_local_store=config.SANDBOX_REAP_ALLOW_LOCAL_STORE,
    )
    if start_reaper:
        try:
            from api.services.sandbox_reaper import SandboxReaper
            _sandbox_reaper = SandboxReaper(store)
            _sandbox_reaper.start()
            if not store_is_shared:
                # opt-in 路径:契约提醒。多 worker 误配在此会删活沙盒,留显眼 WARNING。
                logger.warning(
                    "Sandbox reaper running under a process-local store "
                    "(SANDBOX_REAP_ALLOW_LOCAL_STORE=true): safe ONLY with exactly one "
                    "worker/replica. Multiple workers without Redis WILL cross-delete live "
                    "sandboxes."
                )
        except Exception:
            logger.exception("Sandbox reaper bootstrap failed; continuing without it")
            _sandbox_reaper = None
    else:
        logger.info(f"Sandbox reaper not started: {reason}")

    logger.info("ArtifactFlow API started successfully")

    yield

    # 关闭
    logger.info("Shutting down ArtifactFlow API...")
    # Runner 先优雅停:在途 turn 跑完/取消 → 各自 _wrapped finally → SandboxSession.close()
    # (部分 close 可能超时/失败)。提前到此(close_globals 内会再调一次,_tasks 已空 →
    # no-op),好让 reaper 在 store/docker 仍存活时做最后一扫,兜住 shutdown 期间漏拆的孤儿
    # (P2:单副本停机后不再有 reaper 收尾,孤儿会一直跑到下次启动)。
    if _sandbox_reaper is not None:
        try:
            await get_execution_runner().shutdown()
        except Exception:
            logger.exception("Execution runner early shutdown failed; continuing")
        try:
            await _sandbox_reaper.final_sweep()
        except Exception:
            logger.exception("Sandbox reaper final sweep failed; continuing")
        try:
            await _sandbox_reaper.stop()
        except Exception:
            logger.exception("Sandbox reaper shutdown failed; continuing")
        _sandbox_reaper = None
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

    # request_id 中间件:必须在 CORS 之前 add_middleware,因为 Starlette 中
    # 后注册 = 更外层。我们要 CORS 在最外层,否则本中间件生成的兜底 500 因缺
    # CORS 头,浏览器读不到 body(也读不到 request_id)。
    app.add_middleware(RequestContextMiddleware)

    # 配置 CORS（最外层）。expose_headers 暴露 X-Request-ID,否则跨域下前端
    # res.headers.get('X-Request-ID') 返回 null(allow_headers 管请求头,
    # expose_headers 才管 JS 可读的响应头)。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        allow_methods=config.CORS_ALLOW_METHODS,
        allow_headers=config.CORS_ALLOW_HEADERS,
        expose_headers=["X-Request-ID"],
    )

    # 全局 ValueError → 400(防御纵深;ACC-04)。业务校验失败大多在 Pydantic
    # schema(返回 422)或路由内显式 HTTPException 处理掉;此 handler 兜住漏到
    # handler 顶层的意外 ValueError(如 bcrypt >72 字节、密码策略在非 schema
    # 路径抛错),映射成 400 而非 500。HTTPException 不受影响(走 Starlette 默认)。
    @app.exception_handler(ValueError)
    async def _value_error_handler(request, exc: ValueError):  # noqa: ANN001
        logger.warning(f"Unhandled ValueError → 400: {exc}")
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "request_id": get_request_id() or None},
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
        admin_tools.router,
        prefix="/api/v1/admin",
        tags=["admin"]
    )
    app.include_router(
        departments.router,
        prefix="/api/v1/departments",
        tags=["departments"]
    )
    app.include_router(
        meta.router,
        prefix="/api/v1/meta",
        tags=["meta"]
    )
    app.include_router(
        skills.router,
        prefix="/api/v1/skills",
        tags=["skills"]
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
