"""服务级配置"""

from typing import List
from pydantic_settings import BaseSettings
from pydantic import ConfigDict


class Settings(BaseSettings):
    """
    服务级配置

    可通过环境变量覆盖配置项（前缀 ARTIFACTFLOW_）。
    """

    model_config = ConfigDict(env_prefix="ARTIFACTFLOW_", case_sensitive=False)

    # 服务器配置
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    DEBUG: bool = False

    # CORS 配置
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]  # Next.js 开发服务器
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]

    # SSE 配置
    SSE_PING_INTERVAL: int = 15  # 秒，保持连接活跃
    EXECUTION_TIMEOUT: int = 1800   # 秒，总执行上限（含 permission 等待），同时用作 stream lifetime
    STREAM_CLEANUP_TTL: int = 60    # 秒，执行结束后 consumer 读取剩余事件的清理窗口
    PERMISSION_TIMEOUT: int = 300  # 秒，单次 permission 等待超时

    # Compaction / Context 配置
    COMPACTION_THRESHOLD: int = 180000       # chars, 触发跨轮 compaction（与 context_manager len() 同口径）
    COMPACTION_PRESERVE_PAIRS: int = 2       # 保留最近 N 对不 compact
    COMPACTION_TIMEOUT: int = 600            # 秒, compaction 后台任务超时
    CONTEXT_MAX_CHARS: int = 240000          # context 最大字符数
    TOOL_INTERACTION_PRESERVE: int = 6       # 轮内 tool interaction 尾部保留条数
    INVENTORY_PREVIEW_LENGTH: int = 200     # artifact 清单内容预览截断长度

    # Redis（空 = InMemory fallback，非空 = Redis）
    REDIS_URL: str = ""
    REDIS_CLUSTER: bool = False           # 生产 Cluster 模式
    REDIS_KEY_PREFIX: str = ""             # Redis key 命名空间前缀（共用 Cluster 必须配置）
    REDIS_MAX_CONNECTIONS: int = 50       # 连接池上限
    LEASE_TTL: int = 90  # 秒，心跳每 TTL/3 续租

    # 并发控制
    MAX_CONCURRENT_TASKS: int = 10  # 最大并发引擎执行数

    # 上传限制
    MAX_UPLOAD_SIZE: int = 20 * 1024 * 1024  # 20MB

    # 分页默认值
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # 数据库配置
    DATABASE_URL: str = ""
    DATABASE_URLS: str = ""               # 逗号分隔多 PX 地址（优先级高于 DATABASE_URL）
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 300       # 缩短回收周期，加速故障检测和恢复回切

    # JWT 认证配置
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    @property
    def effective_database_url(self) -> str:
        """统一的有效数据库 URL — 所有消费者（应用、Alembic、脚本）都应使用此属性。

        优先取 DATABASE_URLS 的第一个地址，回落到 DATABASE_URL。
        """
        if self.DATABASE_URLS:
            first = self.DATABASE_URLS.split(",")[0].strip()
            if first:
                return first
        return self.DATABASE_URL


# 全局配置实例
config = Settings()


def validate_config() -> None:
    """Validate required config values. Called during app lifespan startup."""
    if not config.effective_database_url:
        raise RuntimeError(
            "ARTIFACTFLOW_DATABASE_URL environment variable is not set. "
            "Example: ARTIFACTFLOW_DATABASE_URL=sqlite+aiosqlite:///data/artifactflow.db\n"
            "See .env.example for more options."
        )
    if config.REDIS_URL and not config.REDIS_KEY_PREFIX:
        raise RuntimeError(
            "ARTIFACTFLOW_REDIS_KEY_PREFIX must be set when Redis is enabled. "
            "Example: ARTIFACTFLOW_REDIS_KEY_PREFIX=af"
        )
    if not config.JWT_SECRET:
        raise RuntimeError(
            "ARTIFACTFLOW_JWT_SECRET environment variable is not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
