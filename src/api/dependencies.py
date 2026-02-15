"""
FastAPI 依赖注入

提供请求级别的数据库 session、manager 和 controller 实例。

依赖注入链路：
    HTTP Request
        │
        ▼
    get_db_session()        # 创建独立的 AsyncSession
        │
        ├──► get_artifact_manager()
        ├──► get_conversation_manager()
        └──► get_controller()

并发安全保证：
    - DatabaseManager: 全局单例，管理连接池
    - Checkpointer: 全局单例，LangGraph 状态持久化
    - StreamManager: 全局单例，事件缓冲队列
    - AsyncSession: 请求独立，每个请求创建新的数据库会话
    - Repository/Manager/Controller: 请求独立，绑定到请求的 session
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.stream_manager import StreamManager
    from api.services.task_manager import TaskManager

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import config
from core.controller import ExecutionController
from core.conversation_manager import ConversationManager
from core.graph import create_multi_agent_graph, create_async_sqlite_checkpointer
from tools.implementations.artifact_ops import ArtifactManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ============================================================
# 全局单例（跨请求共享）
# ============================================================

_db_manager: Optional[DatabaseManager] = None
_checkpointer: Any = None  # AsyncSqliteSaver，LangGraph 状态持久化
_stream_manager: Optional["StreamManager"] = None
_task_manager: Optional["TaskManager"] = None


async def init_globals() -> None:
    """
    应用启动时初始化全局单例

    在 FastAPI lifespan 中调用。
    """
    import os
    from pathlib import Path

    global _db_manager, _checkpointer, _stream_manager, _task_manager

    # 0. 确保 data 目录存在
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Data directory ensured: {data_dir.absolute()}")

    # 1. 初始化数据库管理器
    _db_manager = DatabaseManager(config.DATABASE_URL)
    await _db_manager.initialize()
    logger.info("Database manager initialized")

    # 2. 创建共享的 checkpointer（用于 interrupt/resume）
    _checkpointer = await create_async_sqlite_checkpointer(config.LANGGRAPH_DB_PATH)
    logger.info(f"Checkpointer initialized: {config.LANGGRAPH_DB_PATH}")

    # 3. 创建 StreamManager
    from api.services.stream_manager import StreamManager
    _stream_manager = StreamManager(ttl_seconds=config.STREAM_TTL)
    logger.info("Stream manager initialized")

    # 4. 创建 TaskManager
    from api.services.task_manager import TaskManager
    _task_manager = TaskManager(max_concurrent=config.MAX_CONCURRENT_TASKS)
    logger.info("Task manager initialized")


async def close_globals() -> None:
    """
    应用关闭时清理全局单例

    在 FastAPI lifespan 中调用。
    """
    global _db_manager, _checkpointer, _stream_manager, _task_manager

    # 1. 先关闭 TaskManager（等待运行中的 graph 任务完成）
    if _task_manager:
        await _task_manager.shutdown()
        logger.info("Task manager shut down")

    # 2. 关闭 checkpointer 的 aiosqlite 连接
    if _checkpointer and hasattr(_checkpointer, 'conn'):
        await _checkpointer.conn.close()
        logger.info("Checkpointer connection closed")

    # 3. 关闭数据库管理器
    if _db_manager:
        await _db_manager.close()
        logger.info("Database manager closed")

    _task_manager = None
    _checkpointer = None
    _db_manager = None
    _stream_manager = None


def get_task_manager() -> "TaskManager":
    """
    获取 TaskManager 单例

    Returns:
        TaskManager 实例
    """
    if _task_manager is None:
        raise RuntimeError("TaskManager not initialized. Call init_globals() first.")
    return _task_manager


def get_stream_manager() -> "StreamManager":
    """
    获取 StreamManager 单例

    Returns:
        StreamManager 实例
    """
    if _stream_manager is None:
        raise RuntimeError("StreamManager not initialized. Call init_globals() first.")
    return _stream_manager


def get_checkpointer() -> Any:
    """
    获取 Checkpointer 单例

    Returns:
        AsyncSqliteSaver 实例
    """
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialized. Call init_globals() first.")
    return _checkpointer


def get_db_manager() -> DatabaseManager:
    """
    获取 DatabaseManager 单例

    Returns:
        DatabaseManager 实例
    """
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialized. Call init_globals() first.")
    return _db_manager


# ============================================================
# 请求级别依赖（每个请求独立）
# ============================================================

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    每个请求获得独立的数据库 session

    请求成功 → 自动 commit
    请求失败 → 自动 rollback

    Yields:
        AsyncSession: 数据库会话
    """
    db_manager = get_db_manager()
    async with db_manager.session() as session:
        yield session


async def get_artifact_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ArtifactManager:
    """
    每个请求获得独立的 ArtifactManager（绑定到请求的 session）

    Args:
        session: 数据库会话（自动注入）

    Returns:
        ArtifactManager 实例
    """
    repo = ArtifactRepository(session)
    return ArtifactManager(repo)


async def get_conversation_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ConversationManager:
    """
    每个请求获得独立的 ConversationManager（绑定到请求的 session）

    Args:
        session: 数据库会话（自动注入）

    Returns:
        ConversationManager 实例
    """
    repo = ConversationRepository(session)
    return ConversationManager(repo)


async def get_controller(
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> ExecutionController:
    """
    每个请求获得独立的 Controller

    注意：
    - Graph 每次创建新实例，因为它持有 artifact_manager 引用
    - 但 checkpointer 是共享的，以支持跨请求的 interrupt/resume
    - create_multi_agent_graph 是 async 函数

    Args:
        artifact_manager: ArtifactManager 实例（自动注入）
        conversation_manager: ConversationManager 实例（自动注入）

    Returns:
        ExecutionController 实例
    """
    compiled_graph = await create_multi_agent_graph(
        artifact_manager=artifact_manager,
        checkpointer=get_checkpointer()  # 使用共享的 checkpointer
    )
    return ExecutionController(
        compiled_graph,
        artifact_manager=artifact_manager,
        conversation_manager=conversation_manager
    )


# ============================================================
# 用户认证依赖
# ============================================================

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
) -> "TokenPayload":
    """
    获取当前已认证用户

    每次请求查 DB 校验 is_active 和最新 role，
    确保禁用/降权即时生效（不仅依赖 JWT payload）。

    Returns:
        TokenPayload（user_id, username, role）
    """
    from api.services.auth import decode_access_token, TokenPayload
    from repositories.user_repo import UserRepository

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # 查 DB 校验用户当前状态
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(payload.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User disabled or not found")

    # 用 DB 中的最新 role 覆盖 JWT 中的 role
    return TokenPayload(user_id=user.id, username=user.username, role=user.role)


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
