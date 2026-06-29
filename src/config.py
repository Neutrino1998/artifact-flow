"""服务级配置"""

from typing import Dict, List
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
    COMPACTION_TOKEN_THRESHOLD: int = 100000  # tokens, LLM 单次调用 input+output 超此值触发引擎内 compaction
    # 上一轮 input+output / 阈值 ≥ 此比例时，向 agent 注入 <context_usage> 预警(临近 compaction
    # → 提示把要据此动作的状态落 artifact)。隐藏实现旋钮，模型不可见(见 CLAUDE.md 工具参数面最小化)。
    CONTEXT_USAGE_WARN_RATIO: float = 0.8
    COMPACTION_TIMEOUT: int = 300            # 秒, 单次 compact LLM 调用超时（thinking 模型压缩 ~100k token 输入需较长 TTFT+生成时间，120s 偏紧）
    INVENTORY_PREVIEW_LENGTH: int = 200     # artifact 清单内容预览截断长度
    READ_ARTIFACT_MAX_CHARS: int = 50000    # read_artifact 默认字符上限（隐藏，模型不可见）
    TOOL_PERSIST_PREVIEW_LENGTH: int = 1000  # 工具结果落盘后回填给模型的预览长度
    SEARCH_TOOLS_MAX_RESULTS: int = 15      # search_tools 单次渲染完整 doc 的工具数上限（隐藏）；
                                            # 超出只列名，防把整集 schema 灌爆下一次 call（压缩不兜底 tool-result overflow）
    # ARTIFACT_CREATED / ARTIFACT_UPDATED(rewrite)整文事件的体积上限。超限则事件
    # 只带"已变更"信号(content 省略、content_omitted=True),前端靠 COMPLETE 后的
    # DB 对齐补全(对齐本就兜底)。update 的 span delta 不受此限(权威且体量随模型输出)。
    ARTIFACT_LIVE_CONTENT_MAX_CHARS: int = 256000
    # Artifact 二进制存储(ArtifactBlob)单条字节上限。写入侧 loud-fail(不静默截断)。
    # 隐藏常量,非模型可调。刻意高于 MAX_UPLOAD_SIZE(100MB):留 2× 余量给 C 阶段沙盒
    # 回写的 blob(模型自生成,不走上传路径),免得再调一次。**ops 依赖**:200MB 单行
    # 要求 MySQL/TDSQL 服务端 max_allowed_packet 抬到其上(默认常仅 16–64MB),否则大
    # insert 在驱动层失败;且跨中心复制 200MB 行成本不低,值随该上限演进再核。
    ARTIFACT_BLOB_MAX_BYTES: int = 200 * 1024 * 1024
    # 识图:read_artifact 把图注入上下文前 resize 到最长边 ≤ 此值(像素),应用侧控
    # token 成本可预测(不靠 provider 的 HF processor)。原始 blob 不变,只降采样注入副本。
    # 1568 对齐主流 VLM 的高分辨率 tile 上限,既清晰又不爆 token。隐藏常量,非模型可调。
    VISION_IMAGE_MAX_EDGE: int = 1568
    # 解压炸弹闸:像素总数(w*h)上限。Pillow 默认仅在 ~178M 像素抛错、89–178M 段只
    # `warnings.warn`(图照常打开)——一张纯色 10000×10000(100M 像素)PNG 可压到十几 KB,
    # 轻松绕过 MAX_UPLOAD_SIZE,落到 read 路径 resize 时才解码,撑爆 CPU/内存。故在**解码前**
    # (Image.open 只读头、拿 size,不解码)显式校验 w*h:上传校验侧拒(loud-fail)、read 侧防御性
    # 再校验。50M 像素 ≈ 50MP,宽于真实相机/截图,远低于 DoS 量级。隐藏常量,operator 可调。
    VISION_IMAGE_MAX_PIXELS: int = 50 * 1000 * 1000

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

    # 上传限制。单文件字节上限(API 边界 loud 422)。批量**总**字节由代理层
    # client_max_body_size 独立封顶(200MB,见 deploy/nginx.conf|Caddyfile):
    # 允许「1 个大文件 or 多个小文件」但控总量——单文件 100MB、数量 10、总量 200MB
    # 三轴独立,总量刻意 < 100MB×10。前端经 /api/v1/meta 取此值做 UX 预挡(后端权威)。
    MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100MB
    # 文本转换路径(DocConverter._convert_text)的独立、更低字节闸。文本是唯一无自身
    # 成本 envelope 的转换路径:charset 检测 + str(best) + split() 会**物化整份解码
    # 内容 + 词列表**,内存放大远超输入字节,且跑在 event loop 上(2026-05-14 wedge 同类)。
    # docx/pdf 存原 blob 不解析(C-0 起 blob-only)、图片存原 blob 不物化文本,只有裸
    # 文本会随 100MB 上传上限线性放大 → 给它保留旧的 20MB envelope。字节上界是首要护栏
    # (to_thread 只缓解 loop 阻塞,解不了内存)。隐藏常量,operator 可调。
    MAX_TEXT_CONVERT_BYTES: int = 20 * 1024 * 1024  # 20MB
    # per-用户 blob 存储配额(字节)。该用户**所有 blob 字节之和**(ArtifactBlob.size_bytes
    # 跨其全部会话)+ 本次新增若超此值 → 拒。**写入侧守门在唯一 chokepoint
    # create_from_upload**(所有 blob 都经此:上传 + 沙盒 persist + 未来任何路径,一处校验
    # 全覆盖,不逐路径加闸)——上传转 413、沙盒 persist 转 ToolResult 让模型提示用户清理。
    # /chat 另有 HTTP 预闸做 fail-fast(起 turn 前拒、零 DB 状态),非唯一守门。只数 blob
    # —— 二进制是"狂传大文件"灌爆盘的主向量(尤其沙盒 persist:无 nginx body / 数量闸,
    # 纯靠它兜底),文本(Artifact.content)有 MAX_TEXT_CONVERT_BYTES 兜着、量级小,刻意不计
    # (进度条同口径,标"附件占用"非"总盘")。account = SUM(size_bytes) compute-on-read(DB
    # 唯一真相,不存计数器),靠 ix_artifact_blobs_session_size 走 index-only;校验计入
    # 「DB 已落 + 本轮已 stage 未 flush」否则一轮多次 persist 各自只看 DB 会齐齐击穿。
    # 软上限:跨会话并发可略微超额,挡量级非字节级。0 = 不限(禁用)。operator 经 env 可调。
    ARTIFACT_USER_QUOTA_BYTES: int = 2 * 1024 * 1024 * 1024  # 2GB

    # 沙盒（C 阶段;隐藏常量,operator 经 env 可调,模型不可见）。
    # DooD:镜像 / 挂载 / runtime 全部固定在代码侧 —— 容器创建参数绝不可被模型
    # 生成内容污染(backend 持 docker.sock = host root,这是硬安全边界)。
    SANDBOX_IMAGE: str = "artifactflow-sandbox:latest"  # scripts/build-sandbox-image.sh 产物
    SANDBOX_RUNTIME: str = ""        # Docker runtime;"" = daemon 默认(本机 dev=runc),prod="runsc"(gVisor)
    # 宿主侧 scratch 工作区根目录。DooD 下 bind-mount 源路径在 **daemon 那台机**解析:
    # backend 容器化部署时必须把同一宿主路径以**相同路径**挂进 backend 容器(经典
    # DooD 同路径要求)。多套部署共用一个 daemon 时各自配不同根目录 —— reaper(C-reap)
    # 以本根目录为第二枚举源,共用根目录会互删对方的 scratch。
    SANDBOX_SCRATCH_ROOT: str = "/tmp/artifactflow-sandbox"
    SANDBOX_COMMAND_TIMEOUT: int = 300  # 秒,单条 bash 命令上限。容器内 `timeout --signal=KILL` 强杀
                                        # (真杀进程);tool 侧另有 +grace 的 asyncio 弃等护栏,只负责
                                        # 提前返回(进程不死,2026-05-14 同型),残留交由 turn 末拆容器兜底。
                                        # 曾兼任"最坏 cancel 延迟上界"(=120);cancel-interrupt 落地后
                                        # (engine 在工具 await 期轮询 cancel → task.cancel 在飞调用,
                                        # core/cancellation.py)该职责剥离,本值只剩 runaway 上界一职,放宽到 300。
    SANDBOX_START_TIMEOUT: int = 60     # 秒,容器 create+start 上限(daemon 卡死时 loud-fail,不 wedge 整 turn)
    SANDBOX_MEM_LIMIT_MB: int = 1024    # 容器内存上限;MemorySwap 设同值 = 禁 swap
    SANDBOX_CPU_LIMIT: float = 1.0      # CPU 核数上限(换算 NanoCpus)
    SANDBOX_PIDS_LIMIT: int = 256       # fork 炸弹闸
    SANDBOX_MAX_OUTPUT_CHARS: int = 200_000  # 单命令输出捕获硬帽:超出继续 drain 但丢弃(防内存放大),
                                             # 截断显式标记。>50k 的部分由引擎溢出转 artifact idiom 接手。
    SANDBOX_STATUS_MAX_ENTRIES: int = 20     # 动态状态注入的工作区第一层清单条数帽:工作区是模型可写的树,
                                             # 不设帽=prompt 注水放大器;超出部分显式 "(+N more)" 标记
    # 磁盘配额(2026-06-10 C′ 方向:loop 池子=硬墙、以下=软配额与准入;host-prep 见 D 段)。
    # prod 把 SANDBOX_SCRATCH_ROOT 挂成定容 loop 文件系统,race 窗口写穿只伤池子不伤宿主。
    SANDBOX_WORKSPACE_QUOTA_MB: int = 2048   # per-turn scratch 软配额:watchdog du 超额 → 杀容器 + sticky 失败
    SANDBOX_WATCHDOG_INTERVAL_SEC: int = 5   # watchdog 巡检周期。探针①:50k 小文件 os.walk ~150ms(线程内),无感
    SANDBOX_POOL_MIN_FREE_MB: int = 1024     # 起容器准入水位:scratch 根所在 fs 剩余低于此拒绝新沙盒(statvfs,O(1))
    SANDBOX_PERSIST_MAX_TEXT_BYTES: int = 20 * 1024 * 1024  # persist 文本判定上限:超此即使可解码也按 blob 存
                                                            # (对齐 MAX_TEXT_CONVERT_BYTES 的量级;blob 上限
                                                            # 复用 ARTIFACT_BLOB_MAX_BYTES,写入侧守门)
    # lease-anchored reaper(C-reap):进程死亡(SIGKILL/OOM,_wrapped finally 不执行)
    # 的二级兜底。资源侧双源枚举(daemon label 容器 + scratch 根目录)− list_active_executions
    # 活跃集 = 孤儿 → 删。最坏烧 CPU 时长 = lease TTL 剩余 + 本间隔(有界,~分钟级)。
    SANDBOX_REAP_ENABLED: bool = True        # 无沙盒部署(无 docker / 不授 bash)置 False,免空轮询刷日志
    SANDBOX_REAP_INTERVAL_SEC: int = 60      # reaper 周期扫间隔(启动立即先扫一次)
    SANDBOX_REAP_GRACE_SEC: int = 60         # 只回收存活 > 此值且无活跃 lease 的资源:躲开
                                             # "刚 lazy 创建 / scratch 刚建、lease 可见性差一拍"的误杀竞态
    # reaper 的跨进程安全要求**共享** liveness 源(Redis):活跃集来自 list_active_executions,
    # InMemory store 只反映本进程 → 多副本/多 worker 下每个进程把兄弟的活沙盒看成无 lease
    # 孤儿、60s 后误删(破坏性,非仅降级)。故 InMemory 下默认不起 reaper;单进程 InMemory
    # (如 Mode-1 轻量部署)要用,操作者在此显式 affirm "我只跑一个进程"。多 worker 一律配 Redis。
    SANDBOX_REAP_ALLOW_LOCAL_STORE: bool = False

    # SSRF / 外联工具防护（隐藏常量，不暴露 API / 工具参数）
    WEB_FETCH_MAX_BYTES: int = 20 * 1024 * 1024   # fallback 下载体上限（解压后字节），
                                                  # 超即中断 —— 防 gzip 炸弹 / 大响应 OOM。
                                                  # 出网下载是独立威胁面,与 MAX_UPLOAD_SIZE
                                                  #（上传,已抬到 100MB）解耦,各自取值。
    # web_fetch 文件旁路:这些 URL 尾缀在 Jina 之前分流为直连下载,以 blob artifact 落盘
    # (而非 Jina 抽文本——对二进制本就坏)。值 = 尾缀 → content_type 兜底(响应头缺失/
    # 撒谎时用)。运行时工具内自决,非模型参数(守「最小化工具参数面」)。
    WEB_FETCH_BLOB_SUFFIXES: Dict[str, str] = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".doc": "application/msword",
        ".xls": "application/vnd.ms-excel",
        ".ppt": "application/vnd.ms-powerpoint",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    CUSTOM_TOOL_SECRET_PREFIX: str = "TOOL_SECRET_"  # 自定义工具 {{VAR}} 只能解析此前缀的环境变量；
                                                     # 把签名密钥 / DB 密码挡在自定义工具可触及范围外

    # 工具凭证主密钥(B-4)。external 工具凭证可逆加密落库(tool_credentials),此密钥
    # 加密/解密用 —— 单把、不轮转、与 JWT_SECRET 同信任模型(DB dump 无此密钥=废密文)。
    # 生成:python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # **强制**:validate_config 缺它即 fail-to-start(同 JWT_SECRET)。无凭证部署也须设——
    # 单点强制换来运行期无「缺 key」分支(reconcile / resolver / set_credential 全假设它在;
    # 缺 = 部署配置错,启动期 loud-fail,不留运行期谜题)。Fernet key 格式(32B urlsafe-base64),
    # 格式错构造即抛。
    CREDENTIAL_KEY: str = ""

    # 输入限制
    MAX_MESSAGE_CHARS: int = 20000   # 单条用户输入 / inject 内容字符上限（超即 422）；
                                     # 超大粘贴在前端转为暂存附件而非 inline 消息
    MAX_INJECT_QUEUE_SIZE: int = 5   # 单轮执行待处理 inject 队列深度上限（满即 429 背压；
                                     # 最坏单次 drain = MAX_MESSAGE_CHARS × 此值，详见输入挡板设计）
    MAX_CHAT_ATTACHMENTS: int = 10   # 单条 /chat 消息附件数量上限（超即 422）；上传后逐个
                                     # 串行转换落库，限制总转换时长 / DB 写入 / 归属串膨胀。
                                     # 注：批量**总**字节由代理层 client_max_body_size(200MB)
                                     # 独立封顶——数量轴管「几个」,总量轴管「多大」,两轴独立。

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
    if not config.CREDENTIAL_KEY:
        raise RuntimeError(
            "ARTIFACTFLOW_CREDENTIAL_KEY environment variable is not set. "
            "It encrypts external-tool credentials at rest (tool_credentials). "
            "Required even with no credentialed tools — single-point enforcement keeps "
            "the runtime free of missing-key branches. Generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    # Validate the Fernet key format at startup (non-empty already checked), so the
    # per-turn snapshot decrypt path never carries a key-validity branch: a malformed
    # key fails loudly here at boot, not silently on every turn. Imported lazily to
    # avoid a config<->credentials import cycle.
    from tools.custom.credentials import CredentialCipher, CredentialKeyError
    try:
        CredentialCipher(config.CREDENTIAL_KEY)
    except CredentialKeyError as e:
        raise RuntimeError(str(e)) from e
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
