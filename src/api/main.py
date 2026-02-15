"""
FastAPI 应用入口

创建 FastAPI 应用实例，配置中间件和路由。
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import config
from api.dependencies import init_globals, close_globals
from api.routers import auth, chat, artifacts, stream
from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    应用生命周期管理

    启动时：初始化全局单例（数据库、checkpointer、StreamManager）
    关闭时：清理资源
    """
    # 启动
    logger.info("Starting ArtifactFlow API...")
    await init_globals()
    logger.info("ArtifactFlow API started successfully")

    yield

    # 关闭
    logger.info("Shutting down ArtifactFlow API...")
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

    # 健康检查端点
    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app


# 创建应用实例
app = create_app()
