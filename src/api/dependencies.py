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

from functools import lru_cache
from typing import AsyncGenerator, Any, Optional

from fastapi import Depends
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


async def init_globals() -> None:
    """
    应用启动时初始化全局单例

    在 FastAPI lifespan 中调用。
    """
    import os
    from pathlib import Path

    global _db_manager, _checkpointer, _stream_manager

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


async def close_globals() -> None:
    """
    应用关闭时清理全局单例

    在 FastAPI lifespan 中调用。
    """
    global _db_manager, _checkpointer, _stream_manager

    # 关闭 checkpointer 的 aiosqlite 连接
    if _checkpointer and hasattr(_checkpointer, 'conn'):
        await _checkpointer.conn.close()
        logger.info("Checkpointer connection closed")

    # 关闭数据库管理器
    if _db_manager:
        await _db_manager.close()
        logger.info("Database manager closed")

    _checkpointer = None
    _db_manager = None
    _stream_manager = None


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
# 预留：用户认证依赖
# ============================================================

async def get_current_user() -> Optional[str]:
    """
    获取当前用户（预留）

    当前返回 None，表示未认证。
    后续可实现 JWT Token 认证。

    Returns:
        用户ID 或 None
    """
    return None
