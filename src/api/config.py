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

    # 分页默认值
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # 数据库配置
    DATABASE_URL: str = "sqlite+aiosqlite:///data/artifactflow.db"
    LANGGRAPH_DB_PATH: str = "data/langgraph.db"

    class Config:
        env_prefix = "ARTIFACTFLOW_"
        case_sensitive = False


# 全局配置实例
config = APIConfig()
