"""
FastAPI 依赖注入

提供全局单例的获取函数和请求级别的数据库 session / manager 实例。

全局单例（init_globals 初始化，跨请求共享）：
    get_db_manager()          # DatabaseManager — 连接池
    get_stream_transport()    # StreamTransport — SSE 事件缓冲队列
    get_task_supervisor()     # TaskSupervisor — 进程内任务监管
    get_conversation_execution_service()  # Conversation admission/runtime commands
    get_runtime_status_reader()            # 只读 live status
    get_agents()              # Agent 配置字典
    get_tools()               # 全局工具字典
    get_mcp_client_manager()  # MCP client manager — per-worker discovery/call facade

请求级依赖（每次 HTTP 请求独立创建）：
    get_db_session()            # AsyncSession
        ├──► get_artifact_service()
        └──► get_conversation_manager() / use-case managers

认证依赖：
    get_current_user()          # 仅交互会话 JWT：账户与管理端点
    get_current_principal()     # 普通用户 API：JWT 或带 scope 的 PAT
        └──► require_admin()    # 管理员权限
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator, Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.stream_transport import StreamTransport
    from api.services.runtime_store import RuntimeStore
    from api.services.conversation_execution_service import ConversationExecutionService
    from api.services.runtime_status_reader import RuntimeStatusReader
    from core.execution.task_supervisor import TaskSupervisor

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from config import config
from core.management.conversation_manager import ConversationManager
from tools.base import BaseTool, build_tool_map
from tools.builtin.artifact_service import ArtifactService
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
_runtime_store: Optional["RuntimeStore"] = None
_task_supervisor: Optional["TaskSupervisor"] = None
_conversation_execution_service: Optional["ConversationExecutionService"] = None
_runtime_status_reader: Optional["RuntimeStatusReader"] = None
_redis_client: Optional[Any] = None               # redis.asyncio.Redis (optional)
_login_rate_limiter: Optional[Any] = None         # Redis / InMemory LoginRateLimiter
_sso_start_rate_limiter: Optional[Any] = None      # Redis / InMemory anonymous admission
_mcp_client_manager: Optional[Any] = None         # tools.custom.mcp_client.McpClientManager
_remote_bearer_config: Optional[Any] = None        # startup-loaded immutable YAML
_sso_state_store: Optional[Any] = None             # Redis / InMemory one-time state
_remote_userinfo_client: Optional[Any] = None      # fixed-origin HTTP client

# Agent configs + tools（启动时加载一次）
_agents: Optional[dict] = None                    # {name: AgentConfig}
_tools: Optional[Dict[str, BaseTool]] = None      # {name: BaseTool}


async def init_globals() -> None:
    """
    应用启动时初始化全局单例

    在 FastAPI lifespan 中调用。
    """
    from pathlib import Path

    global _db_manager, _stream_transport, _runtime_store, _task_supervisor
    global _conversation_execution_service, _runtime_status_reader
    global _redis_client, _agents, _tools
    global _login_rate_limiter, _sso_start_rate_limiter, _mcp_client_manager
    global _remote_bearer_config, _sso_state_store, _remote_userinfo_client

    # 0. 确保 data 目录存在
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Data directory ensured: {data_dir.absolute()}")

    # 0.5 配置期 loud-fail：先于 DB/Redis 等外部资源初始化加载 agent，并校验其
    # 模型别名与 models.yaml 的必填 context_window。坏配置不能等到首个 turn 才暴露，
    # 也不应在拒绝启动前留下已打开的连接池。
    from agents.loader import load_all_agents
    from models.llm import validate_agent_model_config
    _agents = load_all_agents()
    validate_agent_model_config({name: agent.model for name, agent in _agents.items()})
    logger.info(f"Loaded {len(_agents)} agent configs and validated model capabilities")

    from core.security.remote_bearer_config import load_remote_bearer_config
    _remote_bearer_config = load_remote_bearer_config()

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

    # 2+3. RuntimeStore + StreamTransport (Redis or InMemory)

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

        _runtime_store = RedisRuntimeStore(
            _redis_client,
            lease_ttl=config.LEASE_TTL,
            execution_timeout=config.EXECUTION_TIMEOUT,
            permission_timeout=config.PERMISSION_TIMEOUT,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        _runtime_store.init_scripts()

        _stream_transport = RedisStreamTransport(
            _redis_client,
            cleanup_ttl=config.STREAM_CLEANUP_TTL,
            execution_timeout=config.EXECUTION_TIMEOUT,
            ttl_grace=config.STREAM_TTL_GRACE,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        _stream_transport.init_scripts()
        logger.info("Redis runtime initialized (RuntimeStore + StreamTransport)")
    else:
        from api.services.stream_transport import InMemoryStreamTransport
        from api.services.runtime_store import InMemoryRuntimeStore

        _stream_transport = InMemoryStreamTransport(
            ttl_seconds=config.EXECUTION_TIMEOUT + config.STREAM_TTL_GRACE,
            cleanup_ttl=config.STREAM_CLEANUP_TTL,
        )
        _runtime_store = InMemoryRuntimeStore()
        logger.info("InMemory runtime initialized (no REDIS_URL)")

    # SSO state is shared through the same Redis substrate when available.  It
    # is control state, not a disposable cache; the Redis implementation uses
    # one bounded sorted set for atomic expiry cleanup, capacity, and consume.
    if _remote_bearer_config.enabled:
        from core.security.remote_bearer_userinfo import RemoteBearerUserInfoClient
        from core.security.sso_state import InMemorySsoStateStore, RedisSsoStateStore

        if _redis_client is not None:
            _sso_state_store = RedisSsoStateStore(
                _redis_client,
                max_pending=config.SSO_STATE_MAX_PENDING,
                key_prefix=config.REDIS_KEY_PREFIX,
            )
        else:
            _sso_state_store = InMemorySsoStateStore(
                max_pending=config.SSO_STATE_MAX_PENDING
            )
        _remote_userinfo_client = RemoteBearerUserInfoClient(
            _remote_bearer_config,
            max_connections=config.SSO_USERINFO_MAX_CONNECTIONS,
        )
        logger.info(
            "Remote bearer provider enabled: %s (state=%s)",
            _remote_bearer_config.provider.id,
            "Redis" if _redis_client is not None else "InMemory",
        )
    else:
        _sso_state_store = None
        _remote_userinfo_client = None
        logger.info("Remote bearer provider disabled")

    from api.services.conversation_execution_service import ConversationExecutionService
    from api.services.conversation_lease import ConversationLeaseCoordinator
    from api.services.runtime_status_reader import RuntimeStatusReader
    from core.execution.task_supervisor import TaskSupervisor

    _task_supervisor = TaskSupervisor(max_concurrent=config.MAX_CONCURRENT_TASKS)
    lease_ttl = config.LEASE_TTL if config.REDIS_URL else 0
    lease_coordinator = ConversationLeaseCoordinator(
        _runtime_store,
        lease_ttl=lease_ttl,
    )
    _conversation_execution_service = ConversationExecutionService(
        db_manager=_db_manager,
        store=_runtime_store,
        stream_transport=_stream_transport,
        lease_coordinator=lease_coordinator,
        task_supervisor=_task_supervisor,
    )
    _runtime_status_reader = RuntimeStatusReader(
        _runtime_store,
        _runtime_store,
        _stream_transport,
    )

    # 登录频控器：Redis(多 worker 共享)或 InMemory(单机)。
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

    # SSO start counts every anonymous admission rather than authentication
    # failures. Redis makes the global window shared across Backend replicas;
    # the in-memory form is for the existing single-process development mode.
    if _redis_client is not None:
        from api.services.sso_rate_limiter import RedisSsoStartRateLimiter

        _sso_start_rate_limiter = RedisSsoStartRateLimiter(
            _redis_client,
            per_ip_limit=config.SSO_START_IP_MAX_REQUESTS,
            global_limit=config.SSO_START_GLOBAL_MAX_REQUESTS,
            window_seconds=config.SSO_START_RATE_WINDOW_SEC,
            key_prefix=config.REDIS_KEY_PREFIX,
        )
        logger.info("SSO start rate limiter: Redis")
    else:
        from api.services.sso_rate_limiter import InMemorySsoStartRateLimiter

        _sso_start_rate_limiter = InMemorySsoStartRateLimiter(
            per_ip_limit=config.SSO_START_IP_MAX_REQUESTS,
            global_limit=config.SSO_START_GLOBAL_MAX_REQUESTS,
            window_seconds=config.SSO_START_RATE_WINDOW_SEC,
        )
        logger.info("SSO start rate limiter: InMemory")

    # 4. 加载全局工具
    _tools = _load_tools()
    logger.info(f"Loaded {len(_tools)} global tools")

    # 5. MCP client manager(per-worker):每 turn 快照时按已保存 server 配置 lazy discovery。
    from tools.custom.mcp_client import McpClientManager
    _mcp_client_manager = McpClientManager()


def _load_tools() -> Dict[str, BaseTool]:
    """启动时加载进程级全局 builtin 工具（无状态，跨请求共享）。

    external 工具(config/tools/*.md)不在此加载 —— 它们物化进 DB(reconcile),由
    conversation_turn_factory 每 turn 从注册表快照重建。这里只留真正进程级、无状态的
    builtin(web_search / web_fetch / call_subagent / search_tools)；请求级 artifact /
    沙盒工具仍在 conversation_turn_factory 现造。
    """
    from tools.builtin.call_subagent import CallSubagentTool
    from tools.builtin.web_search import WebSearchTool
    from tools.builtin.web_fetch import WebFetchTool
    from tools.builtin.search_tools import SearchToolsTool

    # 从已加载的 agents 推导有效 subagent 列表
    valid_agents = [n for n, c in _agents.items() if n != "lead_agent" and not c.internal] if _agents else None

    tools = [
        CallSubagentTool(valid_agents=valid_agents),
        WebSearchTool(),
        WebFetchTool(),
        # 渐进式披露检索器：进程级注册、无 per-turn 状态；只有显式配置
        # search_tools 的 agent 才会获得它。未配置时 deferred unit 回退为完整 schema。
        SearchToolsTool(),
    ]

    return build_tool_map(tools, [])


async def close_globals() -> None:
    """
    应用关闭时清理全局单例

    在 FastAPI lifespan 中调用。
    """
    global _db_manager, _stream_transport, _runtime_store, _task_supervisor
    global _conversation_execution_service, _runtime_status_reader
    global _redis_client, _login_rate_limiter, _sso_start_rate_limiter
    global _mcp_client_manager
    global _remote_bearer_config, _sso_state_store, _remote_userinfo_client

    # 1. 先关闭 Conversation runtime（唤醒 interrupt，再等任务）
    if _conversation_execution_service:
        await _conversation_execution_service.shutdown()
        logger.info("Conversation execution service shut down")

    # 2. runtime 已 drain，此时才能关闭 provider HTTP 连接池。
    from models.llm import close_llm_clients
    await close_llm_clients()
    logger.info("LLM provider connections closed")

    if _remote_userinfo_client is not None:
        await _remote_userinfo_client.close()
        logger.info("Remote userinfo connection closed")

    # 3. 关闭 Redis 连接
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis connection closed")

    # 4. 关闭数据库管理器
    if _db_manager:
        await _db_manager.close()
        logger.info("Database manager closed")

    _conversation_execution_service = None
    _runtime_status_reader = None
    _task_supervisor = None
    _runtime_store = None
    _redis_client = None
    _db_manager = None
    _stream_transport = None
    _login_rate_limiter = None
    _sso_start_rate_limiter = None
    _mcp_client_manager = None
    _remote_bearer_config = None
    _sso_state_store = None
    _remote_userinfo_client = None


def get_task_supervisor() -> "TaskSupervisor":
    if _task_supervisor is None:
        raise RuntimeError("TaskSupervisor not initialized. Call init_globals() first.")
    return _task_supervisor


def get_conversation_execution_service() -> "ConversationExecutionService":
    if _conversation_execution_service is None:
        raise RuntimeError(
            "ConversationExecutionService not initialized. Call init_globals() first."
        )
    return _conversation_execution_service


def get_runtime_status_reader() -> "RuntimeStatusReader":
    if _runtime_status_reader is None:
        raise RuntimeError("RuntimeStatusReader not initialized. Call init_globals() first.")
    return _runtime_status_reader


def get_runtime_store() -> "RuntimeStore":
    """获取 RuntimeStore 组合实现（非 Router 业务入口）。"""
    if _runtime_store is None:
        raise RuntimeError("RuntimeStore not initialized. Call init_globals() first.")
    return _runtime_store


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


def get_sso_start_rate_limiter() -> Any:
    """Return anonymous SSO start admission shared by all request handlers."""
    if _sso_start_rate_limiter is None:
        raise RuntimeError(
            "SsoStartRateLimiter not initialized. Call init_globals() first."
        )
    return _sso_start_rate_limiter


def get_remote_bearer_config():
    """Return the startup-loaded public-safe provider contract source."""
    if _remote_bearer_config is None:
        raise RuntimeError("Remote bearer config not initialized. Call init_globals() first.")
    return _remote_bearer_config


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


def get_mcp_client_manager():
    """获取 per-worker MCP client manager。"""
    if _mcp_client_manager is None:
        raise RuntimeError("MCP client manager not initialized. Call init_globals() first.")
    return _mcp_client_manager


# ============================================================
# 请求级别依赖（每个请求独立）
# ============================================================

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """每个请求获得独立的数据库 session"""
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


async def get_artifact_service(
    session: AsyncSession = Depends(get_db_session)
) -> ArtifactService:
    """每个请求获得独立的 ArtifactService(自带空 WorkingSet)。

    请求级实例 WorkingSet 恒空、不 bind_emit → 读写自然落到纯 DB。这正是删掉
    旧 _active_managers overlay 后的目标态：REST 读 DB 权威态，turn 中 live 由
    事件流补齐。
    """
    repo = ArtifactRepository(session)
    return ArtifactService(repo)


async def get_conversation_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ConversationManager:
    """每个请求获得独立的 ConversationManager"""
    repo = ConversationRepository(session)
    return ConversationManager(repo)


async def get_tool_registry_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """每个请求获得独立的 ToolRegistryManager（external 工具 CRUD）。"""
    from core.management.tool_registry_manager import ToolRegistryManager
    return ToolRegistryManager(session)


async def get_skill_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """每个请求获得独立的 SkillManager（用户侧 skill 列举 + 个人 toggle）。"""
    from core.management.skill_manager import SkillManager
    return SkillManager(session)


async def get_department_access_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """每个请求获得独立的 DepartmentAccessManager（dept 授权规则）。"""
    from core.management.department_access_manager import DepartmentAccessManager
    return DepartmentAccessManager(session)


async def get_department_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Return the request-scoped department use-case manager."""
    from core.management.department_manager import DepartmentManager
    from repositories.department_repo import DepartmentRepository

    return DepartmentManager(DepartmentRepository(session))


async def get_admin_user_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Return the administrative user use-case manager."""
    from core.management.admin_user_manager import AdminUserManager
    from core.management.department_manager import DepartmentManager
    from repositories.department_repo import DepartmentRepository
    from repositories.user_repo import UserRepository

    department_repository = DepartmentRepository(session)
    return AdminUserManager(
        UserRepository(session),
        department_repository,
        DepartmentManager(department_repository),
        ConversationManager(ConversationRepository(session)),
    )


async def get_user_account_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Return authenticated-user account use cases."""
    from core.management.user_account_manager import UserAccountManager
    from repositories.department_repo import DepartmentRepository
    from repositories.user_repo import UserRepository

    return UserAccountManager(UserRepository(session), DepartmentRepository(session))


async def get_personal_access_token_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Return the session-bound PAT lifecycle/authentication manager."""
    from core.management.personal_access_token_manager import PersonalAccessTokenManager
    from repositories.personal_access_token_repo import PersonalAccessTokenRepository

    return PersonalAccessTokenManager(PersonalAccessTokenRepository(session))


async def get_remote_auth_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Return the request-scoped remote authentication orchestrator."""
    from core.management.department_manager import DepartmentManager
    from core.management.remote_auth_manager import RemoteAuthManager
    from repositories.department_repo import DepartmentRepository
    from repositories.user_repo import UserRepository

    if _remote_bearer_config is None:
        raise RuntimeError("Remote bearer config not initialized. Call init_globals() first.")
    departments = DepartmentRepository(session)
    return RemoteAuthManager(
        _remote_bearer_config,
        _sso_state_store,
        _remote_userinfo_client,
        UserRepository(session),
        DepartmentManager(departments),
    )


async def get_site_config_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """每个请求一个 DB-backed 通知配置 Manager。"""
    from core.management.site_config_manager import SiteConfigManager
    from repositories.site_notification_repo import SiteNotificationRepository

    return SiteConfigManager(SiteNotificationRepository(session))


async def get_client_config_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """前端 runtime meta 的真实数据源聚合器。"""
    from core.management.client_config_manager import ClientConfigManager

    return ClientConfigManager(session)


# ============================================================
# 用户认证依赖
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)

# must_change_password 闸门豁免：用户可拉取自身状态和只读运行时密码策略，
# 并完成改密；其余业务接口一律 403。
_PASSWORD_GATE_EXEMPT: set[tuple[str, str]] = {
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/meta"),
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


async def get_current_principal(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> "AuthPrincipal":
    """Authenticate an ordinary-user endpoint with either a JWT session or PAT."""
    from api.services.auth import AuthPrincipal, decode_access_token
    from core.management.personal_access_token_manager import PersonalAccessTokenManager
    from core.security.personal_access_tokens import ALL_PAT_SCOPES, PAT_BEARER_PREFIX
    from repositories.personal_access_token_repo import PersonalAccessTokenRepository
    from repositories.user_repo import UserRepository

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    bearer = credentials.credentials
    credential_type = "session"
    credential_id = None
    scopes = frozenset(ALL_PAT_SCOPES)
    password_version: Optional[int] = None

    if bearer.startswith(PAT_BEARER_PREFIX):
        authenticated_pat = await PersonalAccessTokenManager(
            PersonalAccessTokenRepository(session)
        ).authenticate(bearer)
        if authenticated_pat is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = authenticated_pat.user_id
        credential_type = "pat"
        credential_id = authenticated_pat.token_id
        scopes = authenticated_pat.scopes
    else:
        payload = decode_access_token(bearer)
        if payload is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user_id = payload.user_id
        password_version = payload.password_version

    user = await UserRepository(session).get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User disabled or not found")

    if credential_type == "session" and password_version != user.password_version:
        raise HTTPException(status_code=401, detail="Token invalidated; please log in again")

    if (
        user.must_change_password
        and (request.method, request.url.path) not in _PASSWORD_GATE_EXEMPT
    ):
        raise HTTPException(status_code=403, detail="Password change required")

    # A PAT is always an ordinary-user credential, even when its owner is an
    # administrator. Admin authority remains exclusive to interactive JWTs.
    principal_role = "user" if credential_type == "pat" else user.role
    return AuthPrincipal(
        user_id=user.id,
        username=user.username,
        role=principal_role,
        password_version=user.password_version,
        credential_type=credential_type,
        credential_id=credential_id,
        scopes=scopes,
    )


def require_scope(scope: str) -> Callable[..., Any]:
    """Require one explicit PAT scope; interactive sessions have full user access."""
    async def dependency(
        principal: "AuthPrincipal" = Depends(get_current_principal),
    ) -> "AuthPrincipal":
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=403,
                detail=f"Personal access token requires scope '{scope}'",
            )
        return principal

    return dependency


async def require_admin(
    user: "TokenPayload" = Depends(get_current_user),
) -> "TokenPayload":
    """要求管理员权限"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
