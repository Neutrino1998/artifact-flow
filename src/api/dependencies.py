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
    - StreamManager: 全局单例，事件缓冲队列
    - TaskManager: 全局单例，后台任务 + interrupt 管理
    - AsyncSession: 请求独立，每个请求创建新的数据库会话
    - Repository/Manager/Controller: 请求独立，绑定到请求的 session
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator, Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.stream_manager import StreamManager
    from api.services.task_manager import TaskManager

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import config
from core.conversation_manager import ConversationManager
from tools.base import BaseTool
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
_stream_manager: Optional["StreamManager"] = None
_task_manager: Optional["TaskManager"] = None

# Agent configs + tools（启动时加载一次）
_agents: Optional[dict] = None                    # {name: AgentConfig}
_tools: Optional[Dict[str, BaseTool]] = None      # {name: BaseTool}


async def init_globals() -> None:
    """
    应用启动时初始化全局单例

    在 FastAPI lifespan 中调用。
    """
    from pathlib import Path

    global _db_manager, _stream_manager, _task_manager, _agents, _tools

    # 0. 确保 data 目录存在
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Data directory ensured: {data_dir.absolute()}")

    # 1. 初始化数据库管理器
    _db_manager = DatabaseManager(config.DATABASE_URL)
    await _db_manager.initialize()
    logger.info("Database manager initialized")

    # 2. 创建 StreamManager
    from api.services.stream_manager import StreamManager
    _stream_manager = StreamManager(ttl_seconds=config.STREAM_TTL)
    logger.info("Stream manager initialized")

    # 3. 创建 TaskManager
    from api.services.task_manager import TaskManager
    _task_manager = TaskManager(max_concurrent=config.MAX_CONCURRENT_TASKS)
    logger.info("Task manager initialized")

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
    valid_agents = [n for n in _agents.keys() if n != "lead_agent"] if _agents else None

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

    # 构建 name → tool 映射，检测重名冲突
    # Artifact 工具是请求级别创建的，但名字固定，需要在启动时排除
    _RESERVED_TOOL_NAMES = {"create_artifact", "update_artifact", "rewrite_artifact", "read_artifact"}

    tool_map: Dict[str, BaseTool] = {}
    for tool in tools:
        tool_map[tool.name] = tool

    for tool in custom_tools:
        if tool.name in tool_map or tool.name in _RESERVED_TOOL_NAMES:
            raise ValueError(
                f"Custom tool '{tool.name}' conflicts with a builtin tool. "
                f"Rename it in config/tools/ to avoid shadowing."
            )
        tool_map[tool.name] = tool

    return tool_map


async def close_globals() -> None:
    """
    应用关闭时清理全局单例

    在 FastAPI lifespan 中调用。
    """
    global _db_manager, _stream_manager, _task_manager

    # 1. 先关闭 TaskManager（等待运行中的任务完成）
    if _task_manager:
        await _task_manager.shutdown()
        logger.info("Task manager shut down")

    # 2. 关闭数据库管理器
    if _db_manager:
        await _db_manager.close()
        logger.info("Database manager closed")

    _task_manager = None
    _db_manager = None
    _stream_manager = None


def get_task_manager() -> "TaskManager":
    """获取 TaskManager 单例"""
    if _task_manager is None:
        raise RuntimeError("TaskManager not initialized. Call init_globals() first.")
    return _task_manager


def get_stream_manager() -> "StreamManager":
    """获取 StreamManager 单例"""
    if _stream_manager is None:
        raise RuntimeError("StreamManager not initialized. Call init_globals() first.")
    return _stream_manager


def get_db_manager() -> DatabaseManager:
    """获取 DatabaseManager 单例"""
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialized. Call init_globals() first.")
    return _db_manager


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


async def get_current_user(
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
