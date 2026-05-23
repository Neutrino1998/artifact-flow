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
    CANCEL_CHECK_INTERVAL: float = 0.5  # 秒，LLM 流式输出期间轮询 cancel 的最小间隔（避免每 chunk 一次 Redis GET）

    # Compaction / Context 配置
    COMPACTION_TOKEN_THRESHOLD: int = 80000  # tokens, LLM 单次调用 input+output 超此值触发引擎内 compaction
    COMPACTION_TIMEOUT: int = 120            # 秒, 单次 compact LLM 调用超时
    INVENTORY_PREVIEW_LENGTH: int = 200     # artifact 清单内容预览截断长度
    READ_ARTIFACT_MAX_CHARS: int = 50000    # read_artifact 默认字符上限（隐藏，模型不可见）
    TOOL_PERSIST_PREVIEW_LENGTH: int = 1000  # 工具结果落盘后回填给模型的预览长度

    # Cancel-path Message.response placeholders.
    # 三条 cancel 路径都要写一个非空占位 —— 前端 MessageList 用 node.response 非空
    # gate AssistantMessage 渲染(同时也是事件流容器),空 response 整条消息+事件流
    # 不显示。BY_USER 给 cooperative cancel(用户主动)；BY_SYSTEM 给 lease fencing /
    # shutdown / late-cancel post-processing。Operator 视角的更细分原因走 events 表
    # 的 reason 字段(external_cancel / external_cancel_post_processing)。
    CANCELLED_RESPONSE_BY_USER: str = "*Task cancelled by user*"
    CANCELLED_RESPONSE_BY_SYSTEM: str = "*Task cancelled by system*"
    SESSION_GREP_MAX_TOTAL: int = 200       # grep_artifact session 模式总命中上限（隐藏，不暴露给模型）

    # update_artifact Layer 2 fuzzy match（v6 锚定 + RapidFuzz 校验；详见
    # docs/_archive/ops/incident-2026-05-14-fix-plan.md PR-1 spec）。
    # 所有常量隐藏，模型不可见，仅供算法实现使用。
    ANCHOR_SHINGLE_LEN: int = 6                # shingle 切分长度（最终生效值受鸽巢约束）
    ANCHOR_MIN_USABLE_LEN: int = 3             # 鸽巢推完的 L 低于此值则当场 bail
    ANCHOR_MAX_OCCURRENCES: int = 20           # shingle 在 content 内最多接受的出现次数（超即视为 common）
    MAX_UNIQUE_CENTERS: int = 50               # Step 3 去重后 center 数上限，超即 bail
    MAX_FUZZY_WALL_CLOCK_MS: int = 500         # Step 4 verify 总 wall-clock 上限，超即 bail
    FUZZY_MAX_L_DIST: int = 16                 # 校验编辑距离绝对上限
    FUZZY_MAX_RATIO: float = 0.10              # 校验编辑距离比例上限（取 min）
    MAX_FUZZY_OLD_STR_LEN: int = 10000         # Layer 2 input 长度硬上界（超即 bail_budget；
                                               # 算法侧 m≈400K 后 Step 1-3 Python 开销本身就超 deadline，
                                               # 取 10K 留 ~20× headroom 同时反映 update_artifact 设计意图）

    # Observability 常量(隐藏,不暴露 API)。
    # jsonl 路径必须在持久卷 /app/data 子目录,容器重启 / autoheal 不丢。
    LOOP_LAG_WARN_MS: int = 500                # watchdog 软退化阈值,超即写一行 loop-lag.jsonl + task 栈
    WATCHDOG_DEADMAN_TIMEOUT_MS: int = 10000   # faulthandler deadman switch 超时(heartbeat 不来即 dump 全栈)
    OBS_SAMPLE_INTERVAL_SEC: int = 30          # sampler 周期(loop_lag / RSS / DB pool / Redis 等)
    OBS_LONG_TASK_AGE_SEC: int = 60            # 长时间运行任务门槛(超此值进 /admin/runtime 的 tasks_long_running)
    OBS_METRICS_LOG_PATH: str = "data/observability/metrics.jsonl"
    OBS_LOOP_LAG_LOG_PATH: str = "data/observability/loop-lag.jsonl"
    OBS_JSONL_MAX_MB: int = 50                 # obs jsonl 单文件大小上限,超即 rotate
    OBS_JSONL_BACKUP_COUNT: int = 10           # obs jsonl 保留备份数(.1 ~ .N);默认覆盖 ~800 天
    OBS_MEM_LIMIT_MB: int = 0                  # RSS 高水位告警上界(MB),0=自动:env > cgroup v2 > cgroup v1 > 不告警。
                                               # 显式设置等于 docker-compose `mem_limit: 2g` 的镜像(避免重复 source-of-truth)
    OBS_STDOUT_MIRROR: bool = False            # 是否把 obs jsonl 镜像到 stdout(默认 False:主通道是持久卷;
                                               # 打开作为 "持久卷未挂载" 的兜底,代价是污染主应用日志流 / docker logs)
                                               # env 覆盖:`ARTIFACTFLOW_OBS_STDOUT_MIRROR=true`(env_prefix 强制带前缀)

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

    # 输入限制
    MAX_MESSAGE_CHARS: int = 20000   # 单条用户输入 / inject 内容字符上限（超即 422）；
                                     # 超大粘贴在前端转为暂存附件而非 inline 消息
    MAX_INJECT_QUEUE_SIZE: int = 5   # 单轮执行待处理 inject 队列深度上限（满即 429 背压；
                                     # 最坏单次 drain = MAX_MESSAGE_CHARS × 此值，详见输入挡板设计）
    MAX_CHAT_ATTACHMENTS: int = 10   # 单条 /chat 消息附件数量上限（超即 422）；上传后逐个
                                     # 串行转换落库，限制总转换时长 / DB 写入 / 归属串膨胀。
                                     # 注：原始上传带宽 / 临时盘占用属代理层（nginx client_max_body_size）

    # 批量导入用户（CSV）
    MAX_BULK_IMPORT_ROWS: int = 1000          # 行数上限，超过整体拒绝（防误传）
    MAX_BULK_IMPORT_BYTES: int = 5 * 1024 * 1024  # 5MB 字节上限（先于行数检查，防恶意大文件）

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
