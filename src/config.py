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
    EXECUTION_TIMEOUT: int = 1800   # 秒，引擎循环执行上限（含 permission 等待）；超时 → TIMED_OUT 终态
    STREAM_CLEANUP_TTL: int = 60    # 秒，执行结束后 consumer 读取剩余事件的清理窗口
    # Redis stream/meta key 寿命 = EXECUTION_TIMEOUT + 此余量。必须覆盖引擎 deadline
    # 之后的 post-processing —— 终态(含 TIMED_OUT)在引擎超时后才由 post-processing
    # push,key 不能在那之前过期(否则 push_event 落在已过期 key 上 → 终态丢失 / SSE 挂)。
    # 取 post-processing 的宽松上界(几次 DB 写 × retry × command_timeout);给太长仅让
    # 崩溃残留 key 多活一会儿(有界自清)。close_stream 正常结束时会重置为 STREAM_CLEANUP_TTL。
    STREAM_TTL_GRACE: int = 300
    PERMISSION_TIMEOUT: int = 300  # 秒，单次 permission 等待超时
    CANCEL_CHECK_INTERVAL: float = 0.5  # 秒，LLM 流式输出期间轮询 cancel 的最小间隔（避免每 chunk 一次 Redis GET）

    # Compaction / Context 配置
    COMPACTION_TOKEN_THRESHOLD: int = 80000  # tokens, LLM 单次调用 input+output 超此值触发引擎内 compaction
    COMPACTION_TIMEOUT: int = 300            # 秒, 单次 compact LLM 调用超时（thinking 模型压缩 ~80k token 输入需较长 TTFT+生成时间，120s 偏紧）
    INVENTORY_PREVIEW_LENGTH: int = 200     # artifact 清单内容预览截断长度
    READ_ARTIFACT_MAX_CHARS: int = 50000    # read_artifact 默认字符上限（隐藏，模型不可见）
    TOOL_PERSIST_PREVIEW_LENGTH: int = 1000  # 工具结果落盘后回填给模型的预览长度
    # ARTIFACT_CREATED / ARTIFACT_UPDATED(rewrite)整文事件的体积上限。超限则事件
    # 只带"已变更"信号(content 省略、content_omitted=True),前端靠 COMPLETE 后的
    # DB 对齐补全(对齐本就兜底)。update 的 span delta 不受此限(权威且体量随模型输出)。
    ARTIFACT_LIVE_CONTENT_MAX_CHARS: int = 256000

    # Cancel-path Message.response placeholders.
    # 三条 cancel 路径都要写一个非空占位 —— 前端 MessageList 用 node.response 非空
    # gate AssistantMessage 渲染(同时也是事件流容器),空 response 整条消息+事件流
    # 不显示。BY_USER 给 cooperative cancel(用户主动)；BY_SYSTEM 给 lease fencing /
    # shutdown / late-cancel post-processing。Operator 视角的更细分原因走 events 表
    # 的 reason 字段(external_cancel / external_cancel_post_processing)。
    CANCELLED_RESPONSE_BY_USER: str = "*Task cancelled by user*"
    CANCELLED_RESPONSE_BY_SYSTEM: str = "*Task cancelled by system*"
    # 超时占位:执行超过 EXECUTION_TIMEOUT 时 Message.response 写入的显示串。
    # 与 CANCELLED_RESPONSE_BY_* 同构 —— 前端 MessageList 用 response 非空 gate
    # AssistantMessage 渲染,超时同样需要非空占位。operator 视角的"超时"语义走
    # events 表的 TIMED_OUT 终态事件,不靠这个串区分。
    TIMED_OUT_RESPONSE: str = "*Task timed out*"
    SESSION_GREP_MAX_TOTAL: int = 200       # grep_artifact session 模式总命中上限（隐藏，不暴露给模型）
    # grep_artifact 资源护栏（隐藏常量，模型不可见）。设计原则:grep 是 line-oriented 的
    # best-effort 搜索 —— 把**输入/输出 envelope** 一次性定死,envelope 内全物化才安全,
    # 超出即截断 + surface "search incomplete"。不为对抗性巨输入逐 pass 补 cap（详见
    # docs/_archive/reviews/sec-review-findings.md「Reviewer 复审收口」第 3 轮）。
    # 注意全部是 **CPU/扫描护栏**,不是内存护栏:session 峰值内存由"载入多少"决定（list
    # 查询 eager-load `Artifact.content` + cache 累积）,那是**有意接受的 best-effort**
    # （真 bound 需 repo 列投影 + 绕 cache,对内存从未爆过的 🟡 不划算,见 GREP-02）。
    GREP_CONTENT_MAX_CHARS: int = 2_000_000         # 单 artifact 扫描字符上限。**值由"pre-scan 物化保持有界"反推,
                                                    # 非"artifact 最大能多大"**:_scan_content 先对整篇 splitlines×2 +
                                                    # 建 line_starts,成本 O(行数)。2MB 最坏(全 "x\n",100万行)≈102MB /
                                                    # 520ms,有界;20MB 会 ~1GB(reviewer P1)。超即截断扫描量 + surface
    GREP_MAX_LINE_CHARS: int = 1000                 # 单行进结果的字符上限（ripgrep --max-columns 式）。挡"单条巨行
                                                    # 命中→整行塞进 ToolResult"（reviewer P2:5M 行→5M body）。超即截断 + 标记
    GREP_SESSION_SCAN_BUDGET_CHARS: int = 16_000_000  # session 单次调用聚合扫描字符预算（很多中等 artifact 时限总扫描功 + splitlines）
    GREP_MAX_SCAN_MATCHES: int = 200_000            # finditer 原始命中迭代上界,**per 工具调用**(session 模式跨 artifact
                                                    # 累计共享,不是每个 artifact 重置 —— 否则 200 个密集单行 artifact 累计
                                                    # 40M 迭代、~86s 同步 wedge,reviewer round 4)。max_count 只数"去重后的
                                                    # 行",单行海量命中时永远到不了它 → finditer 被抽干(同步 CPU wedge,
                                                    # 2026-05-14 同源失败模式的另一个轴)。mirror update_artifact 的
                                                    # MAX_UNIQUE_CENTERS:cap 真正烧 CPU 的量。实测 200K 原始命中 ≈380ms
                                                    # < watchdog 500ms(20M 单行从 ~35s 收到 ≈380ms);legit 密集文档
                                                    # (如 1000 行×100 列 CSV grep "," ≈100K)仍放行。session 循环另在每
                                                    # artifact 间 `await asyncio.sleep(0)` 让出事件循环(不 wedge + 可取消)
    GREP_MAX_PATTERN_CHARS: int = 1000              # pattern 长度上界（挡病态超长 pattern；RE2 另有 max_mem=8MiB 编译侧兜底）
    GREP_MAX_CONTEXT: int = 100                     # context 行数上界（防超大窗口铺满全文）
    GREP_MAX_COUNT: int = 1000                      # max_count 上界（去重后行级命中数）

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

    # SSRF / 外联工具防护（隐藏常量，不暴露 API / 工具参数）
    WEB_FETCH_MAX_BYTES: int = 20 * 1024 * 1024   # fallback 下载体上限（解压后字节），
                                                  # 超即中断 —— 防 gzip 炸弹 / 大响应 OOM；
                                                  # 与 MAX_UPLOAD_SIZE / DocConverter 对齐
    CUSTOM_TOOL_SECRET_PREFIX: str = "TOOL_SECRET_"  # 自定义工具 {{VAR}} 只能解析此前缀的环境变量；
                                                     # 把签名密钥 / DB 密码挡在自定义工具可触及范围外

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
    # PG per-语句 wall-clock(秒)。后处理不在引擎超时(EXECUTION_TIMEOUT)内 —— per-query
    # 上界是 DB 层职责。仅 PostgreSQL(asyncpg)生效:setdefault 注入 connect_args。取"比最慢
    # 的合法查询还宽、远小于 EXECUTION_TIMEOUT"。
    # 禁用:设本项=0(ARTIFACTFLOW_DB_COMMAND_TIMEOUT=0)→ 不注入。
    # ⚠️ 不能用 DSN ?command_timeout=0 禁用 —— asyncpg 拒绝 ≤0(connect_utils.py),会启动失败;
    #    DSN 若显式给值必须 >0,它会覆盖此默认。
    # MySQL/TDSQL 无等价 driver 钩子(靠 server innodb_lock_wait_timeout),SQLite 无此缺口。
    # 详见 docs/architecture/execution-lifecycle.md「不变量 4」。
    DB_COMMAND_TIMEOUT: float = 30.0

    # JWT 认证配置
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    # 密码策略（等保 9.1.4.1 身份鉴别;隐藏常量,operator 可调,不暴露 API/工具参数）。
    # 强度档(2026-05-25 定标):等保四级基线 —— ≥8 位、须含字母+数字+符号三类全、
    # 拒弱口令/键盘序列黑名单。周期改密(降等保三级):全部用户 180 天到期 + 不重用前 1 次。
    PASSWORD_MIN_LENGTH: int = 8              # 静态口令长度下限(等保「8 位以上」)
    PASSWORD_REQUIRE_LETTER: bool = True      # 须含字母(大小写均算)
    PASSWORD_REQUIRE_DIGIT: bool = True       # 须含数字
    PASSWORD_REQUIRE_SYMBOL: bool = True      # 须含符号(等保「字母、数字、符号混合」三类全)
    PASSWORD_EXPIRY_DAYS: int = 180           # 口令到期天数,超期登录即置 must_change_password;0=不强制到期
    PASSWORD_HISTORY_COUNT: int = 1           # 新密码不得与「最近 N 个用过的口令(含当前)」相同;1=仅当前
    PASSWORD_HISTORY_RETAIN: int = 5          # password_history 列保留的历史 hash 数。从 day 1 起维护,
                                              # 故调高 PASSWORD_HISTORY_COUNT(≤RETAIN+1)即生效、无需再迁移。
                                              # 这是 history-count 解耦 retain 的关键:列存得比当前查得多。

    # 登录频控(ACC-01;隐藏常量)。per-username + per-IP 各自单键计数,Cluster 安全
    # (绝不跨键 multi-key)。失败累计超阈 → 429 锁定至窗口过期。
    LOGIN_MAX_FAILURES: int = 5               # 窗口内最大失败次数,达到即拒
    LOGIN_FAILURE_WINDOW_SEC: int = 900       # 失败计数滑窗 / 锁定时长(秒),15 分钟

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
    # CORS footgun guard (DEP-01): with credentials enabled, Starlette reflects
    # the request Origin whenever CORS_ORIGINS contains "*", which silently turns
    # an env misconfig (ARTIFACTFLOW_CORS_ORIGINS='["*"]') into "any site may read
    # authenticated responses". The default config is a concrete allowlist (safe);
    # this assertion stops the env override from being applied silently. Wildcard
    # origins are only ever valid with credentials disabled.
    if config.CORS_ALLOW_CREDENTIALS and "*" in config.CORS_ORIGINS:
        raise RuntimeError(
            "CORS_ALLOW_CREDENTIALS=True is incompatible with a '*' entry in "
            "CORS_ORIGINS. Starlette reflects the request Origin in this combination, "
            "allowing any site to read authenticated responses. List explicit origins "
            "(e.g. ARTIFACTFLOW_CORS_ORIGINS='[\"https://app.example.com\"]'), or set "
            "ARTIFACTFLOW_CORS_ALLOW_CREDENTIALS=false if wildcard origins are truly intended."
        )
