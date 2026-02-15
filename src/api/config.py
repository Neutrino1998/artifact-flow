"""
API 配置

包含服务器配置、CORS 配置、SSE 配置等。
"""

from typing import List
from pydantic_settings import BaseSettings


class APIConfig(BaseSettings):
    """
    API 配置类

    可通过环境变量覆盖配置项。
    """

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True

    # CORS 配置
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]  # Next.js 开发服务器
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]

    # SSE 配置
    SSE_PING_INTERVAL: int = 15  # 秒，保持连接活跃
    STREAM_TIMEOUT: int = 300    # 秒，最大执行时间
    STREAM_TTL: int = 30         # 秒，队列 TTL（前端未连接时自动清理）

    # 并发控制
    MAX_CONCURRENT_TASKS: int = 10  # 最大并发 Graph 执行数

    # 分页默认值
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # 数据库配置
    DATABASE_URL: str = "sqlite+aiosqlite:///data/artifactflow.db"
    LANGGRAPH_DB_PATH: str = "data/langgraph.db"

    # JWT 认证配置
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    class Config:
        env_prefix = "ARTIFACTFLOW_"
        case_sensitive = False


# 全局配置实例
config = APIConfig()

# Fail-fast: JWT secret 必须显式配置
if not config.JWT_SECRET:
    raise RuntimeError(
        "ARTIFACTFLOW_JWT_SECRET environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )
