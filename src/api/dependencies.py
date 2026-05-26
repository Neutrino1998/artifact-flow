"""
FastAPI 依赖注入

提供全局单例的获取函数和请求级别的数据库 session / manager 实例。

全局单例（init_globals 初始化，跨请求共享）：
    get_db_manager()          # DatabaseManager — 连接池
    get_stream_transport()    # StreamTransport — SSE 事件缓冲队列
    get_execution_runner()    # ExecutionRunner — 后台任务调度 + RuntimeStore
    get_agents()              # Agent 配置字典
    get_tools()               # 全局工具字典

请求级依赖（每次 HTTP 请求独立创建）：
    get_db_session()            # AsyncSession
        ├──► get_artifact_manager()
        ├──► get_conversation_manager()
        └──► get_user_repository()

认证依赖：
    get_current_user()          # JWT 校验 + DB 查活
        └──► require_admin()    # 管理员权限
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator, Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.stream_transport import StreamTransport
    from api.services.runtime_store import RuntimeStore
    from api.services.execution_runner import ExecutionRunner

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from core.conversation_manager import ConversationManager
from tools.base import BaseTool, build_tool_map
from tools.builtin.artifact_ops import ArtifactManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ============================================================
# 全局单例（跨请求共享）
# ============================================================

_db_manager: Optional[DatabaseManager] = None
_stream_transport: Optional["StreamTransport"] = None
_execution_runner: Optional["ExecutionRunner"] = None
_redis_client: Optional[Any] = None               # redis.asyncio.Redis (optional)
_login_rate_limiter: Optional[Any] = None         # Redis / InMemory LoginRateLimiter

# Agent configs + tools（启动时加载一次）
_agents: Optional[dict] = None                    # {name: AgentConfig}
_tools: Optional[Dict[str, BaseTool]] = None      # {name: BaseTool}


async def init_globals() -> None:
    """
    应用启动时初始化全局单例

    在 FastAPI lifespan 中调用。
    """
    from pathlib import Path

    global _db_manager, _stream_transport, _execution_runner, _redis_client, _agents, _tools
    global _login_rate_limiter

    # 0. 确保 data 目录存在
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Data directory ensured: {data_dir.absolute()}")

    # 1. 初始化数据库管理器
    db_urls = [u.strip() for u in config.DATABASE_URLS.split(",") if u.strip()] if config.DATABASE_URLS else []

    _db_manager = DatabaseManager(
        database_url=config.effective_database_url,
        database_urls=db_urls if len(db_urls) > 1 else None,
        pool_size=config.DATABASE_POOL_SIZE,
        max_overflow=config.DATABASE_MAX_OVERFLOW,
        pool_timeout=config.DATABASE_POOL_TIMEOUT,
        pool_recycle=config.DATABASE_POOL_RECYCLE,
        command_timeout=config.DB_COMMAND_TIMEOUT,
    )
    await _db_manager.initialize()
    logger.info("Database manager initialized")

    # 2+3. StreamTransport + ExecutionRunner (Redis or InMemory)
    from api.services.execution_runner import ExecutionRunner

    if config.REDIS_URL:
        from redis.asyncio import Redis, RedisCluster
        from redis.backoff import ExponentialBackoff
        from redis.retry import Retry
        from api.services.redis_runtime_store import RedisRuntimeStore
        from api.services.redis_stream_transport import RedisStreamTransport

        retry = Retry(ExponentialBackoff(cap=2, base=0.1), retries=3)

        if config.REDIS_CLUSTER:
            _redis_client = RedisCluster.from_url(
                config.REDIS_URL,
                decode_responses=True,
                max_connections=config.REDIS_MAX_CONNECTIONS,
                retry=retry,
                retry_on_timeout=True,
            )
        else:
            _redis_client = Redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                max_connections=config.REDIS_MAX_CONNECTIONS,
                retry=retry,
                retry_on_timeout=True,
            )
        await _redis_client.ping()  # fail fast
        logger.info(f"Redis connected: {config.REDIS_URL}")

        runtime_store = RedisRuntimeStore(
            _redis_client,
            lease_ttl=config.LEASE_TTL,
            execution_timeout=config.EXECUTION_TIMEOUT,
            permission_timeout=config.PERMISSION_TIMEOUT,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        runtime_store.init_scripts()

        _stream_transport = RedisStreamTransport(
            _redis_client,
            cleanup_ttl=config.STREAM_CLEANUP_TTL,
            execution_timeout=config.EXECUTION_TIMEOUT,
            ttl_grace=config.STREAM_TTL_GRACE,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        _stream_transport.init_scripts()
        _execution_runner = ExecutionRunner(
            max_concurrent=config.MAX_CONCURRENT_TASKS,
            store=runtime_store,
            lease_ttl=config.LEASE_TTL,
        )
        logger.info("Redis runtime initialized (RuntimeStore + StreamTransport)")
    else:
        from api.services.stream_transport import InMemoryStreamTransport
        from api.services.runtime_store import InMemoryRuntimeStore

        _stream_transport = InMemoryStreamTransport(ttl_seconds=config.STREAM_CLEANUP_TTL)
        _execution_runner = ExecutionRunner(
            max_concurrent=config.MAX_CONCURRENT_TASKS,
            store=InMemoryRuntimeStore(),
        )
        logger.info("InMemory runtime initialized (no REDIS_URL)")

    # 3.5 登录频控器（ACC-01）。Redis(多 worker 共享)或 InMemory(单机)。
    if _redis_client is not None:
        from api.services.login_rate_limiter import RedisLoginRateLimiter
        _login_rate_limiter = RedisLoginRateLimiter(
            _redis_client,
            max_failures=config.LOGIN_MAX_FAILURES,
            window_sec=config.LOGIN_FAILURE_WINDOW_SEC,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        logger.info("Login rate limiter: Redis")
    else:
        from api.services.login_rate_limiter import InMemoryLoginRateLimiter
        _login_rate_limiter = InMemoryLoginRateLimiter(
            max_failures=config.LOGIN_MAX_FAILURES,
            window_sec=config.LOGIN_FAILURE_WINDOW_SEC,
        )
        logger.info("Login rate limiter: InMemory")

    # 4. 加载 Agent 配置
    from agents.loader import load_all_agents
    _agents = load_all_agents()
    logger.info(f"Loaded {len(_agents)} agent configs")

    # 5. 加载全局工具
    _tools = _load_tools()
    logger.info(f"Loaded {len(_tools)} global tools")


def _load_tools() -> Dict[str, BaseTool]:
    """启动时加载全局工具（无状态，跨请求共享）"""
    from tools.builtin.call_subagent import CallSubagentTool
    from tools.builtin.web_search import WebSearchTool
    from tools.builtin.web_fetch import WebFetchTool
    from tools.custom.loader import load_custom_tools

    # 从已加载的 agents 推导有效 subagent 列表
    valid_agents = [n for n, c in _agents.items() if n != "lead_agent" and not c.internal] if _agents else None

    # 内置工具
    tools = [
        CallSubagentTool(valid_agents=valid_agents),
        WebSearchTool(),
        WebFetchTool(),
    ]

    # 自定义工具（从 config/tools/*.md 加载）
    custom_tools = load_custom_tools()
    if custom_tools:
        logger.info(f"Loaded {len(custom_tools)} custom tool(s): {[t.name for t in custom_tools]}")

    return build_tool_map(tools, custom_tools)


async def close_globals() -> None:
    """
    应用关闭时清理全局单例

    在 FastAPI lifespan 中调用。
    """
    global _db_manager, _stream_transport, _execution_runner, _redis_client, _login_rate_limiter

    # 1. 先关闭 ExecutionRunner（等待运行中的任务完成）
    if _execution_runner:
        await _execution_runner.shutdown()
        logger.info("Execution runner shut down")

    # 2. 关闭 Redis 连接
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis connection closed")

    # 3. 关闭数据库管理器
    if _db_manager:
        await _db_manager.close()
        logger.info("Database manager closed")

    _execution_runner = None
    _redis_client = None
    _db_manager = None
    _stream_transport = None
    _login_rate_limiter = None


def get_execution_runner() -> "ExecutionRunner":
    """获取 ExecutionRunner 单例"""
    if _execution_runner is None:
        raise RuntimeError("ExecutionRunner not initialized. Call init_globals() first.")
    return _execution_runner


def get_runtime_store() -> "RuntimeStore":
    """获取 RuntimeStore 单例（从 ExecutionRunner 获取）"""
    return get_execution_runner().store


def get_stream_transport() -> "StreamTransport":
    """获取 StreamTransport 单例"""
    if _stream_transport is None:
        raise RuntimeError("StreamTransport not initialized. Call init_globals() first.")
    return _stream_transport


def get_db_manager() -> DatabaseManager:
    """获取 DatabaseManager 单例"""
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialized. Call init_globals() first.")
    return _db_manager


def get_redis_client() -> Optional[Any]:
    """获取 Redis 客户端（未配置 Redis 时返回 None）"""
    return _redis_client


def get_login_rate_limiter() -> Any:
    """获取登录频控器单例（Redis / InMemory）。"""
    if _login_rate_limiter is None:
        raise RuntimeError("LoginRateLimiter not initialized. Call init_globals() first.")
    return _login_rate_limiter


def get_agents() -> dict:
    """获取 Agent 配置字典"""
    if _agents is None:
        raise RuntimeError("Agents not loaded. Call init_globals() first.")
    return _agents


def get_tools() -> Dict[str, BaseTool]:
    """获取全局工具字典"""
    if _tools is None:
        raise RuntimeError("Tools not loaded. Call init_globals() first.")
    return _tools


# ============================================================
# 请求级别依赖（每个请求独立）
# ============================================================

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个请求获得独立的数据库 session"""
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


async def get_artifact_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ArtifactManager:
    """每个请求获得独立的 ArtifactManager"""
    repo = ArtifactRepository(session)
    return ArtifactManager(repo)


async def get_conversation_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ConversationManager:
    """每个请求获得独立的 ConversationManager"""
    repo = ConversationRepository(session)
    return ConversationManager(repo)


# ============================================================
# 用户认证依赖
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)

# must_change_password 闸门豁免:仅这两个 (method, path) 在标志为 True 时仍放行,
# 让用户能拉到自己的状态(GET /me)并完成改密(POST /me/password)。其余一律 403。
_PASSWORD_GATE_EXEMPT: set[tuple[str, str]] = {
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/me/password"),
}


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> "TokenPayload":
    """
    获取当前已认证用户

    每次请求查 DB 校验 is_active 和最新 role。
    """
    from api.services.auth import decode_access_token, TokenPayload
    from repositories.user_repo import UserRepository

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(payload.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User disabled or not found")

    # 密码已被修改 → 老 token 失效（pwd_v 比对）
    if payload.password_version != user.password_version:
        raise HTTPException(status_code=401, detail="Token invalidated; please log in again")

    # 强制改密闸门(门类三):首次登录 / admin 重置 / 口令到期 → 除改密+查自身
    # 状态外一律 403。前端据此弹不可关闭的改密框;这是后端侧的防御兜底
    # (即便绕过前端,业务端点也进不去)。
    if user.must_change_password and (request.method, request.url.path) not in _PASSWORD_GATE_EXEMPT:
        raise HTTPException(status_code=403, detail="Password change required")

    return TokenPayload(
        user_id=user.id,
        username=user.username,
        role=user.role,
        password_version=user.password_version,
    )


async def require_admin(
    user: "TokenPayload" = Depends(get_current_user),
) -> "TokenPayload":
    """要求管理员权限"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> "UserRepository":
    """获取 UserRepository 实例"""
    from repositories.user_repo import UserRepository
    return UserRepository(session)


async def get_department_repository(
    session: AsyncSession = Depends(get_db_session),
) -> "DepartmentRepository":
    """获取 DepartmentRepository 实例"""
    from repositories.department_repo import DepartmentRepository
    return DepartmentRepository(session)
