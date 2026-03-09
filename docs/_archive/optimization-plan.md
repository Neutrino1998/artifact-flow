# ArtifactFlow 分阶段优化计划

基于 code review 反馈和 `docs/architecture/concurrency.md` 演进路线整合。

---

## Phase 1: 核心流程 Bug 修复（ID 一致性 + Permission Resume） ✅ DONE

> **完成于**: commit `c24fdb8` — 统一 ID 生成源（Router 层为权威源，Controller 接受外部传入）、前端 PermissionModal 改从 streamStore 读取 ID、前端处理 metadata 事件做一致性校验、resume 归属校验（thread_id ↔ conversation_id 绑定）。

---

## Phase 2: 安全加固 ✅ DONE

> **完成于**: commit `0e0eb23` — web_fetch SSRF 防护（协议校验 + 私网 IP 拒绝 + permission AUTO→CONFIRM）、持久化 fail fast（移除静默吞异常）、Docker healthcheck（/docs→/health）、错误信息脱敏（`_sanitize_error_event`）。附带修复 permission 前端确认流程。补丁：`65a78f1`、`b1fe826`。

---

## Phase 3: 数据质量改善 (3.1 ✅, 3.2 ✅, 3.3 ⏸️)

> **3.1 & 3.2 完成于**: commit `14f44e6` — 分页 total 真实计数（Repository 层 count 查询）、Artifact created_at 返回 DB 真实时间。
> **补丁**: commit `925ce9f` — list_conversations 去掉 asyncio.gather 避免共享 AsyncSession；create_artifact 缓存构造传入 db_artifact.created_at。

### 3.3 Graph 编译缓存（来自 concurrency.md Phase 3） ⏸️ Deferred

**当前状态**: 暂不实施，先保持“每请求编译 graph”现状，后续在有明确性能瓶颈数据后再推进。

**背景问题**: 当前每个请求都会创建 Agent/Tool/Registry 并编译 StateGraph，存在固定 CPU 开销，影响吞吐。

**现有实现特征（2026-02）**:
- `src/api/dependencies.py` 和 `src/api/routers/chat.py` 的后台任务路径中，均会按请求调用 `create_multi_agent_graph()`
- `artifact_manager`（请求级对象，绑定请求级 DB session）当前通过闭包/实例字段参与 graph 构建
- 该设计在“每请求编译”前提下并发正确性可接受（不会跨请求复用 session），但吞吐较差

**候选改造方案（已调研，未落地）**:
- 目标：启动时编译一次 graph 并缓存为全局单例；请求级 `artifact_manager` 通过 runtime context 注入
- 对齐 LangGraph 推荐模式：`context_schema` + `runtime.context`（context 不进入 checkpoint，resume 时需重新传入）

1. 新增 `GraphContext`
- 新建 `src/core/graph_context.py`
- 定义 dataclass：`GraphContext(artifact_manager: Optional[ArtifactManager])`
- 作用：承载请求级依赖，避免 graph 闭包捕获 request-scoped 对象

2. 改造 `core/graph.py`
- `ExtendableGraph` 移除 `artifact_manager` 构造参数
- `StateGraph` 增加 `context_schema=GraphContext`
- 节点执行时通过 runtime 读取 `artifact_manager`，传入 `ContextManager.build_agent_messages(...)`
- `create_multi_agent_graph()` 改为不接收 `artifact_manager`

3. 改造 `tools/implementations/artifact_ops.py`
- Artifact 工具不再在 `__init__` 持有 manager
- 执行时从 runtime context 获取 manager
- `create_artifact_tools()` 改为无参工厂

4. 改造 `api/dependencies.py`
- 增加 `_compiled_graph` 全局变量
- 在 `init_globals()` 中编译并缓存 graph
- 增加 `get_compiled_graph()` 访问器
- `get_controller()` 改为复用缓存 graph，仅注入请求级 manager

5. 改造 `core/controller.py`
- 所有 graph 调用点（`ainvoke`/`astream`、new/resume）统一传入 `context=GraphContext(...)`

6. 改造 `api/routers/chat.py`
- 两个后台任务（`execute_and_push` / `execute_resume`）改为复用 `get_compiled_graph()`
- 删除任务内重复编译 graph 的逻辑

7. 测试适配
- `tests/test_core_graph.py` / `tests/test_core_graph_stream.py`：测试环境改为“graph 编译一次 + 请求级 manager 复用 graph”

**关键注意事项（必须满足）**:
- `compiled_graph` 绝不能持有 request-scoped 对象（尤其 `ArtifactManager` / `AsyncSession`）
- 开启 graph 缓存后，graph 内绑定的 tool/agent 实例会跨请求复用；所有工具需保证无状态或并发安全
- `web_fetch` 等工具若持有可变实例状态（配置/运行对象），应改为执行期局部创建，避免跨请求状态污染
- 需锁定支持 runtime context 的 LangGraph 版本，避免环境解析到旧版本导致运行时错误
- context 不会持久化到 checkpoint，resume 路径必须每次重新传入 context

**为何先 defer**:
- 当前更关注并发正确性与稳定性，优先避免一次性引入“缓存 + 依赖注入模型切换 + tool 生命周期变化”的复合改动风险
- Phase 5/6（Redis + PostgreSQL 迁移）完成后再结合压测数据评估，收益/风险比更清晰

**后续触发条件（再开启本项）**:
- 有明确数据表明 graph 编译耗时显著影响 p95/p99 或吞吐
- 完成工具无状态化审计（至少 `artifact_ops`、`web_fetch`）
- 有可自动化回归覆盖 new/resume/streaming/并发双会话

---

## Phase 4: 认证框架 ✅ DONE

> **完成于**: commit `8d367ae` — JWT 认证框架（签发/验证、User 模型、get_current_user 依赖注入、所有路由 user_id 过滤、SSE 认证、resume 归属校验、admin 用户管理 API）。前端：登录页、authStore、AuthGuard、401 拦截。CLI：login/logout + token 持久化。补丁：`821f20a`、`b573be1`、`0a06643`。

---

## Phase 5: Redis 引入 + Alembic 迁移框架

**目标**: 解决最紧迫的并发瓶颈（checkpointer 单连接串行），引入 Alembic 迁移框架为 Phase 6 PostgreSQL 切换铺路。

> **执行顺序调整说明**: 原计划 Phase 5 PostgreSQL → Phase 6 Redis，但当前最明确的并发瓶颈是 checkpointer 单连接串行（`concurrency.md` L105-108），应优先解决。Redis 前置可更快获得并发收益，PostgreSQL 迁移改为 Phase 6。

### 5.1 RETURNING 移除 + Alembic 迁移框架

**动机**: 消除唯一的方言依赖（RETURNING 子句），引入 Alembic 管理 schema 变更。不建方言适配层 — Repository 层已全部通过 ORM 构造查询，天然方言无关。

**涉及文件**:
- `src/repositories/artifact_repo.py` — 移除 `.returning()` 调用
- `src/db/migrations/` — Alembic 初始化

**改动**:

**5.1.1 移除 RETURNING 子句**

`artifact_repo.py:324` 的乐观锁更新使用了 `.returning()`，MySQL/TDSQL 不支持。改为 `rowcount` 判断 + 计算新版本号。改动量极小（全项目仅此一处）。

**5.1.2 Alembic 迁移框架**

替换手写 SQL 迁移（`001_initial_schema.py` 使用原生 SQL 含 `AUTOINCREMENT`、`INSERT OR IGNORE` 等 SQLite 方言）：

- `alembic init` 初始化，`env.py` 配置 async engine
- 从当前 ORM models 生成初始迁移（`alembic revision --autogenerate`）
- 迁移脚本中使用 SQLAlchemy DDL API，不写方言专属 SQL
- 迁移执行策略：**部署前单次执行**（CLI 命令或 init container），不在应用启动时自动 `upgrade head`（多 worker 同时启动会导致并发迁移竞态）
- `DatabaseManager.initialize()` 改为 **schema version 校验**：启动时检查当前 DB 版本是否匹配预期，不匹配则 fail fast 并提示运行迁移命令
- 旧迁移脚本 `001_initial_schema.py` 直接删除

**退出标准**:
- RETURNING 已移除，Repository 层零方言依赖
- Alembic 迁移正常执行
- 现有回归测试全部通过

---

### 5.2 Redis 基础设施 + Checkpointer 迁移

**动机**: `AsyncSqliteSaver` 使用单个 `aiosqlite.connect()` 连接，内部只有一个后台线程 + 队列，所有 checkpoint 操作串行执行。N 个并发用户的所有 checkpoint 读写排队，是当前最大的全局延迟瓶颈。迁移到 Redis 可立即获得并发读写能力。

**基础设施前置条件**:
- Redis 必须使用 **Redis Stack**（`redis/redis-stack` Docker 镜像），因为 `langgraph-checkpoint-redis` 依赖 RedisJSON + RediSearch 模块
- 验证：`redis-cli MODULE LIST` 输出中需包含 `ReJSON` 和 `search`

**涉及文件**:
- `docker-compose.yml` — 新增 Redis Stack 服务
- `src/api/config.py` — 新增 `REDIS_URL`、`CHECKPOINT_TTL`
- `src/core/graph.py` — 替换 `create_async_sqlite_checkpointer()`（L662-713）
- `src/api/dependencies.py` — 替换 checkpointer 初始化/清理（L60-93, L96-122）
- `requirements.txt` — 新增 `langgraph-checkpoint-redis`、`redis[hiredis]`

**改动**:

**5.2.1 Redis Stack Docker 配置**

```yaml
redis:
  image: redis/redis-stack:7.4.0-v3    # 版本 pin，不用 latest
  ports:
    - "6379:6379"
    - "8001:8001"    # RedisInsight（开发调试用，生产可移除）
  volumes:
    - redis_data:/data
  command: >
    redis-stack-server
    --appendonly yes
    --appendfsync everysec
    --save 900 1
    --save 300 10
    --maxmemory 512mb
    --maxmemory-policy noeviction
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    timeout: 5s
    retries: 3
```

关键配置说明：
- **版本 pin**: `7.4.0-v3`（或当时最新稳定版），避免 `latest` 导致不可预期变更
- **AOF 持久化**: `appendonly yes` + `appendfsync everysec`（平衡性能与数据安全）
- **RDB 快照**: `save 900 1` / `save 300 10`（兜底恢复）
- **内存策略**: `noeviction` — checkpoint 是核心状态，不允许被 LRU 淘汰；内存满时返回 OOM 错误，由应用层返回 503
- **单节点部署**: 当前阶段不引入 Sentinel/Cluster。单节点限制：无自动故障转移，Redis 进程挂掉需手动重启。后续如需 HA，升级路径为 Redis Sentinel（主从 + 自动故障转移）或云托管 Redis

**5.2.2 Checkpointer 迁移**

`src/core/graph.py` — 新增 `create_redis_checkpointer()` 替换 `create_async_sqlite_checkpointer()`：

```python
async def create_redis_checkpointer(redis_url: str, ttl: dict | None = None):
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    checkpointer = AsyncRedisSaver.from_conn_string(redis_url, ttl=ttl)
    await checkpointer.setup()
    return checkpointer
```

`src/api/config.py` 新增：
- `REDIS_URL: str = "redis://localhost:6379"`
- `CHECKPOINT_TTL: int = 86400`（24 小时，秒）

`src/api/dependencies.py` 改动：
- `init_globals()`: `create_redis_checkpointer(config.REDIS_URL, ttl={"checkpoint_ns": config.CHECKPOINT_TTL})` 替换 `create_async_sqlite_checkpointer()`
- `close_globals()`: 替换 `checkpointer.conn.close()` 为 Redis 连接关闭
- 移除 `LANGGRAPH_DB_PATH` 配置项和 `data/langgraph.db` 文件依赖

**5.2.3 故障处理**

当前执行路径是 `POST /chat` → `task_manager.submit()` → 立即返回 200 → background task 执行 graph。Redis 故障如果发生在 background task 阶段，HTTP 响应已经返回，无法追溯改为 503。因此需要区分两种场景：

- **启动时** Redis 不可用 → `init_globals()` 抛异常，应用启动失败（fail fast），不对外提供服务
- **请求时** Redis 已知不可用 → **submit 前健康门控**：在 `POST /chat` 和 `resume` 的 router 入口处，调用 `redis.ping()` 探活；失败则直接返回 HTTP 503 + `{"detail": "Checkpoint service unavailable"}`，不提交 background task
  - 涉及文件：`src/api/routers/chat.py` L195, L441（`task_manager.submit()` 调用前）
  - 实现方式：新增 FastAPI 依赖 `require_redis_healthy()` 或在 router 函数入口显式检查
- **执行中** Redis 断连（已通过健康门控但执行过程中断连）→ background task 捕获异常，推送 SSE error 事件 `{"type": "error", "data": {"error": "Checkpoint service unavailable"}}`，前端展示错误提示
- **TTL 过期** → resume 时 checkpointer 查无数据 → HTTP 410 Gone + `{"detail": "会话状态已过期，请重新发送消息"}`
- 日志记录 Redis 连接状态变化，便于运维监控

**退出标准**:
- `checkpointer.setup()` 成功，interrupt/resume 端到端通过
- TTL 自动过期验证（设短 TTL → 等待 → resume 得到 410）
- 启动时 Redis 不可用 → 应用启动失败（fail fast 验证）
- 请求时 Redis 不可用 → 健康门控返回 503（submit 前拦截验证）
- 执行中 Redis 断连 → SSE error 事件推送到前端
- 现有回归测试全部通过（checkpointer 替换对上层透明）

---

### 5.3 StreamManager 迁移到 Redis Streams

**动机**: 当前 `asyncio.Queue` 仅支持单进程部署。Pub/Sub 断线不可回放，与"断线重连不丢消息"目标冲突。Redis Streams 是唯一满足可靠投递需求的方案。

**涉及文件**:
- `src/api/services/stream_manager.py` — 核心重构
- `src/api/dependencies.py` — Redis 连接复用（5.2 已建立）
- `src/api/routers/stream.py` — 支持 `Last-Event-ID` header

**改动**:

**5.3.1 为什么选 Redis Streams（非 Pub/Sub）**

| 特性 | Pub/Sub | Redis Streams |
|------|---------|---------------|
| 断线重放 | ❌ 消费者不在线则消息丢失 | ✅ 基于 ID 从断点重放 |
| 消息持久化 | ❌ 内存中即时投递 | ✅ 持久化到 AOF/RDB |
| 消费者组 | ❌ | ✅ 支持多消费者 + ACK |
| 历史消息查询 | ❌ | ✅ XRANGE / XREAD |
| 背压控制 | ❌ | ✅ MAXLEN / XTRIM |

**5.3.2 StreamManager 重构设计**

核心模型：
- 每个 `thread_id` 对应一个 Redis Stream key: `stream:{thread_id}`
- 事件写入: `XADD stream:{thread_id} MAXLEN ~1000 * event_type {type} data {json}`
- 事件消费: `XREAD BLOCK {timeout} STREAMS stream:{thread_id} {last_id}`
- 断线重连: 前端通过 `Last-Event-ID` header 传入上次收到的 event ID → 从该 ID 之后读取
- 清理: Stream key 设置 TTL（`EXPIRE stream:{thread_id} {config.STREAM_TTL}`），终结事件后到期自动删除
- 所有权隔离: Stream metadata 中存储 `owner_user_id`，消费时校验

StreamManager 对外 API 保持不变（对上层透明）：
- `create_stream(thread_id, owner_user_id)` → 创建 Stream key + 存储 owner + 设置 TTL
- `push_event(thread_id, event)` → `XADD`，返回 False 当 stream 已关闭（信号 background task 停止）
- `consume_events(thread_id, last_event_id?)` → `XREAD BLOCK` async generator，支持心跳
- `close_stream(thread_id)` → 推送终结事件 + 标记关闭（保留 TTL 窗口供断线重连）

内部移除：
- `_streams: dict` → 不再需要内存字典
- `asyncio.Queue` → 替换为 Redis Streams
- `asyncio.Lock` → Redis 原子操作天然并发安全
- TTL asyncio.Task → Redis `EXPIRE` 原生支持

**5.3.3 SSE 端点支持断线重连**

`src/api/routers/stream.py` 改动：
- 读取 `Last-Event-ID` request header
- 传入 `consume_events(thread_id, last_event_id=...)`
- SSE 响应中每个事件附带 `id:` 字段（使用 Redis Stream entry ID，如 `1234567890-0`）
- **前端需改动**：我们使用 fetch + ReadableStream（非 EventSource），浏览器不会自动处理 `Last-Event-ID`。需在 `frontend/src/lib/sse.ts` 中手动维护 `last_event_id`（从每个 SSE 事件的 `id:` 字段提取），断线重连时通过 `Last-Event-ID` request header 携带

**Redis 故障下 `GET /stream` 的行为约定**（补充 5.2.3 仅覆盖 POST/resume 的缺口）：

- **连接建立时** Redis 不可用（`XREAD` 失败）→ 返回 HTTP 503（SSE 连接未建立，可用 HTTP 状态码）
- **流传输中** Redis 断连 → 发送 SSE error 事件 `{"type": "error", "data": {"error": "Stream service unavailable"}}`，关闭 SSE 连接。前端可用 `last_event_id` 尝试重连
- 与 5.2.3 的语义一致：请求入口可拦截时用 HTTP 状态码，已进入流传输则用 SSE error 事件

**退出标准**:
- 跨请求事件投递成功（POST push → GET consume）
- 断线重连测试：消费中断 → 用 `last_event_id` 重连 → 不丢消息
- TTL 自动清理验证（Stream key 过期后被删除）
- 多 worker 场景测试（两个进程，一个 push 一个 consume）
- 所有权隔离测试（非 owner 消费被拒绝）
- Redis 故障测试：`GET /stream` 连接建立时 503 + 流中断时 SSE error
- 现有回归测试全部通过

---

### 5.4 Manager 缓存决策

**决策: ConversationManager 和 ArtifactManager 均保持 request-local 内存缓存，不迁移到 Redis。**

**覆盖范围**:
- `ConversationManager._cache`（`conversation_manager.py` L88）— `Dict[str, ConversationCache]`
- `ArtifactManager._cache`（`artifact_ops.py` L187）— `Dict[str, Dict[str, ArtifactMemory]]`

两者是同构模式：都是请求级实例内的 Python dict，生命周期与请求绑定。

**理由**:
- 两个 Manager 都是请求级实例（`dependencies.py` L192 ArtifactManager, L208 ConversationManager），每次请求创建新实例
- `_cache` dict 天然短生命周期（随请求结束 GC），不存在跨请求状态共享问题
- 迁移到 Redis 会增加每次读操作的网络 RTT（~0.1ms local dict → ~1ms Redis），无实际收益
- Phase 6 PostgreSQL 迁移后，数据库查询性能足以支撑无缓存场景

**不做改动**。后续若出现热点查询性能问题，再考虑 Redis 缓存，但仅对特定热点路径，不全量迁移。

---

### 5.5 TaskManager 多 Worker 适配

**动机**: 当前 `TaskManager` 是纯进程内实现（`_tasks: dict` + `asyncio.Semaphore`），只能管理单 worker 内的任务。多 worker 部署下无法防止同一 `thread_id` 被不同 worker 重复执行，也无法做全局并发控制。

**设计原则**: 两层架构 — 保留本地 `TaskManager`（生命周期管理、优雅停机），新增 Redis 分布式协调层。

**涉及文件**:
- `src/api/services/task_manager.py` — 新增分布式锁集成
- `src/api/routers/chat.py` — `submit` 前加锁（L195, L441）
- `src/api/dependencies.py` — Redis 连接注入（复用 5.2 的连接）

**改动**:

**5.5.1 [P1] 分布式锁 — 防重复执行**

在 `task_manager.submit()` 前，对 `thread_id` 加 Redis 分布式锁，防止同一任务被多个 worker 同时执行：

```python
# 伪代码 — chat.py submit 前
lock_key = f"task_lock:{thread_id}"
lock_token = str(uuid4())  # 唯一 token，标识本次加锁者
acquired = await redis.set(lock_key, lock_token, nx=True, ex=config.STREAM_TIMEOUT)
if not acquired:
    raise HTTPException(409, "Task already running for this thread")
# submit 后在 background task finally 中释放锁（必须校验 token）
```

- 锁的 TTL = `STREAM_TIMEOUT`（兜底超时释放，防止 worker 崩溃后死锁）
- **释放时必须校验 token**：使用 Lua 脚本 compare-and-del，防止误删其他 worker 的锁（场景：任务超时 → 锁过期 → 被新 worker 抢占 → 旧任务 finally 不能直接 `DEL`）

```lua
-- Lua compare-and-del（原子操作）
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
```

- resume 端点同理（L441）

**注意**：`thread_id` 锁解决的是"同一任务被多 worker 重复执行"的问题。它**不覆盖**客户端重试幂等性（因为每次 `POST /chat` 会生成新 `thread_id`）。如需防止客户端重试导致重复执行，需额外设计 idempotency key（如前端生成请求级 UUID 通过 header 传入，后端用 Redis `SET NX` 去重）。当前阶段不做，作为后续 Phase 可选项记录。

**5.5.2 [P2] 全局并发上限 ⏸️ Deferred**

当前阶段**保留本地 `Semaphore`** 做 per-worker 并发控制，不做 Redis 全局限流。

**理由**：本地 Semaphore 已能防止单 worker 过载；全局限流（ZSET 信号量 + Lua）增加复杂度但在当前部署规模下收益不明确。

**触发条件**（再开启本项）：
- 压测数据表明多 worker 间并发总量需要全局控制
- 出现过因缺少全局限流导致的资源争抢问题

**候选方案**（已调研，未落地）：ZSET 信号量 + Lua 原子 acquire/release（member=thread_id, score=过期时间戳），acquire 时 ZREMRANGEBYSCORE 清理过期租约 → ZCARD 检查 → ZADD 登记。避免裸 INCR/DECR（崩溃泄漏）、DBSIZE（统计无关 key）、SCAN（O(N) 热路径）。

**5.5.3 [P2] 任务状态登记 ⏸️ Deferred**

将运行中的任务注册到 Redis Hash 提升可观测性。同样等压测或运维需求驱动再实施。

**退出标准**（本轮仅覆盖 5.5.1）:
- 同一 `thread_id` 并发提交到不同 worker → 第二个被拒绝（409）
- Worker 崩溃后锁自动释放（TTL 兜底）
- 本地生命周期管理（引用持有、优雅停机）不受影响

---

## Phase 6: PostgreSQL 迁移

**目标**: 主数据库从 SQLite 切换到 PostgreSQL，获得多写者并发（MVCC）、复合索引性能、生产级连接池。同时移除所有 SQLite 专属代码。

**前置依赖**: Phase 5.1（Alembic）已完成。

### 6.1 数据库引擎切换

**涉及文件**:
- `src/db/database.py` — `DatabaseManager` 简化（移除 SQLite 分支）
- `src/api/config.py` — `DATABASE_URL` 默认值更新 + 连接池参数外部化
- `requirements.txt` — 新增 `asyncpg`，移除 `aiosqlite`
- `docker-compose.yml` — 新增 PostgreSQL 服务

**改动**:

**6.1.1 DatabaseManager 简化 + 连接池外部化**

- 移除 `_is_sqlite()`、`_configure_sqlite_wal()`、PRAGMA 等 SQLite 专属代码
- `DatabaseManager` 只接收 `DATABASE_URL` + 通用连接池参数，零方言分支
- 连接池参数通过 env var 注入（`config.py`），不硬编码：

```python
# config.py
DATABASE_URL: str = "postgresql+asyncpg://artifactflow:changeme@localhost:5432/artifactflow"
DB_POOL_SIZE: int = 10
DB_MAX_OVERFLOW: int = 20
DB_POOL_TIMEOUT: int = 30
DB_POOL_RECYCLE: int = 1800
```

```python
# database.py — 无方言判断
self._engine = create_async_engine(
    url,
    pool_size=config.DB_POOL_SIZE,
    max_overflow=config.DB_MAX_OVERFLOW,
    pool_timeout=config.DB_POOL_TIMEOUT,
    pool_recycle=config.DB_POOL_RECYCLE,
    pool_pre_ping=True,
)
```

未来切库只需换 `DATABASE_URL` + 调整池参数，代码零改动。数据库服务端配置（`statement_timeout`、`work_mem` 等）放在 PostgreSQL 配置侧，不在应用代码中管理。

**6.1.2 Docker PostgreSQL 服务**

```yaml
postgres:
  image: postgres:16-alpine
  environment:
    POSTGRES_DB: artifactflow
    POSTGRES_USER: artifactflow
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U artifactflow"]
    interval: 10s
    timeout: 5s
    retries: 5
```

backend 服务 `depends_on` 增加 `postgres` 和 `redis`（含 health condition）。

### 6.2 Schema 迁移与类型适配

**涉及文件**:
- `src/db/models.py` — 类型审查
- `src/db/migrations/` — Alembic 迁移脚本（5.1.2 已初始化）

**改动**:

**6.2.1 ORM 模型类型审查**

当前模型已基本兼容 PostgreSQL，需确认的点：
- `JSON` → SQLAlchemy 在 PostgreSQL 上自动映射为 `JSONB`（兼容 ✅，可显式标注提升可读性）
- `DateTime` → PostgreSQL `TIMESTAMP`（兼容 ✅）
- `String(N)` → `VARCHAR(N)`（兼容 ✅）
- `Text` → `TEXT`（兼容 ✅）
- `Integer` + `autoincrement=True`（`artifact_versions.id`）→ PostgreSQL `SERIAL`（SQLAlchemy 自动处理 ✅）

数据迁移不需要 — 开发阶段，直接用 Alembic 建表。

### 6.3 性能优化 — 复合索引

**涉及文件**:
- `src/db/models.py` — 新增复合索引定义
- Alembic 迁移脚本

**改动**:

基于热点查询分析新增复合索引：

```python
# conversations 表
# 热点查询：list_conversations() — WHERE user_id=? ORDER BY updated_at DESC
# 文件：conversation_repo.py L210, L213
Index("ix_conversations_user_updated", "user_id", "updated_at")

# messages 表
# 热点查询：conversation 内消息加载 — WHERE conversation_id=? ORDER BY created_at
# 文件：conversation_repo.py L386
Index("ix_messages_conv_created", "conversation_id", "created_at")

# artifact_versions 表
# 热点查询：版本历史 — WHERE artifact_id=? AND session_id=? ORDER BY version
# 已有 UniqueConstraint(artifact_id, session_id, version) 可复用 ✅
```

**退出标准**:
- PostgreSQL 上所有回归测试通过
- PostgreSQL 并发写入测试通过（模拟多请求同时写入 conversation/message/artifact）
- SQLite 专属代码已全部移除（WAL、PRAGMA、`_is_sqlite()`、`aiosqlite` 依赖）
- 复合索引在 EXPLAIN 中被正确使用

---

## 测试策略（贯穿 Phase 5 / Phase 6）

当前测试基座是内存 SQLite（`tests/conftest.py` L50-60）。Phase 6 完成后统一切到 PostgreSQL，测试环境与生产一致。

### 测试基础设施改造

**涉及文件**:
- `tests/conftest.py` — fixture 改用 PostgreSQL（本地 `docker-compose.dev.yml` 或 CI service container）
- `tests/integration/` — 新增集成测试目录

**改动**:

```python
# tests/conftest.py
@pytest.fixture(scope="session")
def db_manager():
    db_url = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://...")
    manager = DatabaseManager(db_url)
    ...
```

**Redis 集成测试**（`tests/integration/`）:
- `test_redis_checkpointer.py` — checkpoint CRUD / TTL 过期 / interrupt-resume
- `test_redis_stream_manager.py` — 事件推送/消费/断线重连/TTL 清理
- `test_redis_fault.py` — Redis 断连后 503 响应、Redis 恢复后自动重连

**并发测试增强**:
- 多请求并发写入 conversation/message/artifact（验证 PostgreSQL MVCC）
- 多 worker stream push/consume（验证 Redis Streams 跨进程投递）

---

## Phase 7: 文件上传 → Artifact

### Phase 7A: 文档上传 + content_type 统一 + 前端渲染修正 ✅ DONE

> **完成于**: commit `78df7c4` — content_type 统一为 MIME type、DocConverter 文档转换层（pandoc + pymupdf，双向导入导出）、上传 API（`POST /artifacts/{session_id}/upload`）+ Artifact.source 字段（`"agent"` / `"user_upload"`）、前端渲染策略修正（Preview tab 仅 `text/markdown`）、前端上传 UI（按钮 + 拖拽，无 session 时禁用）、Prompt 设计 Review（artifact inventory 注入 source 属性 + 行为指引）。补丁：`937bb66`、`6fb8e70`、`e243dbf`、`694ba88`。

---

### Phase 7B: 结构化数据 + 原始文件存储（建议 Phase 5/6 之后）

**依赖**: Phase 7A + Phase 5/6（PostgreSQL 大字段支持 + 可能需要对象存储）

**目标**: 支持 csv / json 等结构化数据上传，保留原始内容供代码沙盒处理，不强制转 markdown。

#### 7B.1 结构化数据上传

**涉及文件**:
- `src/api/routers/artifacts.py` — 上传白名单扩展
- `src/tools/utils/doc_converter.py` — 新增 csv / json 处理
- `frontend/src/components/artifact/` — 可能需要表格预览组件

**改动**:
- 新增支持：`.csv`, `.json`（后续可扩展 `.xlsx` 等）
- csv / json **不强制转 markdown**：`content_type` 保持 `"text/csv"` / `"application/json"`，`content` 存原始文本
- 可选生成 markdown 摘要预览（如 csv 前 20 行转 markdown 表格），存到 `metadata.preview_markdown`
- 前端对 `text/csv` 可后续实现表格渲染组件（不在 7B 范围内，可作为独立增强）

#### 7B.2 原始文件存储（待定）

**说明**: 7A 阶段 docx / pdf 转换后只存 markdown 文本，原始文件不保留。7B 评估是否需要：
- 原始文件 BLOB 存储（数据库）或对象存储（S3 / MinIO）
- 原始文件下载功能
- 取决于是否有"重新转换"或"下载原始文件"的需求

#### 7B.3 代码沙盒联动（前置调研）

**说明**: Agent 使用 Python 工具处理 csv / json 的能力依赖独立的代码沙盒功能（sandbox execution），不在 Phase 7 范围内。7B 需确保 artifact 数据格式兼容未来沙盒读取：
- csv artifact 保持原始文本，沙盒可直接 `pd.read_csv(StringIO(content))`
- json artifact 保持原始文本，沙盒可直接 `json.loads(content)`

---

## Phase 8: 用户直接编辑 Artifact

**目标**: 允许用户通过前端直接编辑 Artifact 内容，与 Agent 协作修订。

### 8.1 后端 Artifact 写接口

**涉及文件**:
- `src/api/routers/artifacts.py` — 新增 PUT/PATCH 端点
- `src/api/schemas/` — 新增更新请求 schema（含 `lock_version` 乐观锁）
- `src/tools/implementations/artifact_ops.py` — 新增 `update_by_user()` 方法
- `src/db/models.py` — Artifact 模型已有 `lock_version` 字段

**改动**:
- `PUT /api/v1/artifacts/{session_id}/{artifact_id}` — 全量更新内容
- 请求体包含 `content` + `lock_version`，乐观锁防止并发冲突
- 更新时创建新 version 记录（`update_type = "rewrite"` 或 `"update"`，取决于编辑范围）
- 返回新的 `lock_version` 供前端下次提交使用

### 8.2 前端编辑 UI

**涉及文件**:
- `frontend/src/components/artifact/` — ArtifactPanel 中增加编辑模式
- `frontend/src/lib/api.ts` — 新增更新 API 调用
- `frontend/src/stores/artifactStore.ts` — 编辑状态管理

**改动**:
- Artifact 预览面板增加"编辑"按钮，切换到编辑模式
- 编辑模式：代码类型用 code editor（monaco-editor 或 CodeMirror），文本类型用 textarea
- 保存时带 `lock_version`，冲突时提示用户（409 Conflict → 显示 diff 让用户选择）
- 乐观更新：保存后立即更新本地状态，失败时回滚

---

## Phase 9: Skill 系统

**目标**: 允许用户管理可复用的知识/技能片段（user-scoped，跨所有会话），Agent 在会话中自动或按需加载。

**状态**: 调研完成，方案已确定（轻量独立表），待排入开发计划。

### 9.1 业界调研结论

**Agent Skills 已形成跨平台开放标准**（[agentskills.io](https://agentskills.io/specification)），Claude Code / Copilot / Windsurf / OpenCode 均采用同一规范。Cursor 是唯一例外（自有 `.mdc` 格式）。

**标准 Skill 文件结构**:
```
.claude/skills/<name>/
├── SKILL.md          # 主指令（必须）— YAML frontmatter + markdown body
├── references/       # 引用资料（可选，按需加载）
├── scripts/          # 可执行脚本（可选）
└── assets/           # 模板/配置（可选）
```

**SKILL.md 格式**:
```yaml
---
name: fix-issue
description: Fix a GitHub issue by number.   # 用于自动匹配
disable-model-invocation: true               # 是否禁止模型自动调用
allowed-tools: Bash(gh *), Read, Write       # 执行期间允许的工具
context: fork                                # 是否在隔离子 agent 中运行
model: claude-opus-4                         # 可选模型覆盖
argument-hint: "[issue-number]"              # 自动补全提示
user-invocable: true                         # 是否显示在 / 菜单
---

Markdown body with instructions...
支持 $ARGUMENTS、$0、${CLAUDE_SESSION_ID}、!`command` 变量替换。
```

**核心架构模式——渐进式披露（Progressive Disclosure）**:

所有系统的共同设计：不全量注入所有 skill 到 context。

| 层级 | 加载内容 | 时机 |
|------|---------|------|
| L1 Metadata | name + description（~100 tokens/skill） | 始终加载，嵌入 tool description |
| L2 Body | SKILL.md 全文（~500-5000 tokens） | 用户 `/invoke` 或模型自动匹配时 |
| L3 References | references/ 下的文件 | 执行中按需读取 |

**Claude Code 内部实现**:
1. 注册 `Skill` 元工具，description 嵌入所有 skill 的 L1 metadata（~15K 字符预算）
2. 模型判断相关时调用 `Skill` tool
3. 系统注入一条**隐藏 user message**（含 SKILL.md 全文）到对话
4. 临时修改工具权限和模型覆盖
5. **核心洞察：Skill 本质是 prompt-based context modifier，不是可执行代码——改变模型怎么想，而不是能做什么**

**激活方式对比**:

| 触发方式 | 系统 |
|----------|------|
| 斜杠命令 `/skill-name` | Claude Code, Copilot |
| @-mention `@skill-name` | Windsurf, Cursor |
| 模型自动匹配（基于 description） | Claude Code, Copilot, Windsurf |
| 文件 glob 模式匹配 | Cursor 独有 |
| 始终激活 | CLAUDE.md, Cursor `alwaysApply: true` |

**Scope 层级**（Claude Code）:

| Scope | 路径 | 作用范围 |
|-------|------|---------|
| Enterprise | Managed settings | 全组织用户 |
| Personal | `~/.claude/skills/<name>/SKILL.md` | 个人所有项目 |
| Project | `.claude/skills/<name>/SKILL.md` | 当前项目 |

### 9.2 设计决策

**关键决策：独立 `skills` 表，不复用 Artifact 表。**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 复用 Artifact 表 + 标记 | 零 schema 变更，版本管理免费 | **Scope 不匹配**：Artifact 是 session-scoped（绑 `conversation_id`），Skill 必须 user-scoped 跨所有会话；语义污染 |
| ✅ 独立 Skill 实体 | 语义清晰，天然 user-scoped，独立生命周期 | 新表 + 新 API（但工作量很小） |

**Skill 本质定位**：静态知识注入（system prompt context modifier），不含可执行脚本。ArtifactFlow 的 tool 能力由已有 ToolRegistry 管理，Skill 只负责"指导模型行为"。

### 9.3 数据模型

```python
class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(64), primary_key=True)        # slug, e.g. "coding-standards"
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)        # 显示名
    description = Column(String(1024), nullable=False) # L1 metadata，用于自动匹配
    content = Column(Text, nullable=False)             # L2 markdown body
    is_active = Column(Boolean, default=True)          # 用户可启用/禁用
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

### 9.4 API 端点

```
POST   /api/v1/skills           # 创建 skill
GET    /api/v1/skills           # 列出当前用户的 skills
GET    /api/v1/skills/{id}      # 获取 skill 详情
PUT    /api/v1/skills/{id}      # 更新 skill
DELETE /api/v1/skills/{id}      # 删除 skill
PATCH  /api/v1/skills/{id}      # 切换 is_active
```

所有端点 `Depends(get_current_user)`，按 `user_id` 隔离。

### 9.5 Context 注入

**注入链路**:

```
ContextManager.prepare_agent_context()
  → skill_repo.list_active_skills(user_id)
  → context["skills_metadata"]  (L1: name + description)
  → LeadAgent.build_system_prompt(context)
    → <available_skills> section in system prompt
```

**加载策略**（根据 skill 数量选择）:

| 场景 | 策略 |
|------|------|
| 少量 skill（<10） | 全量注入 body 到 system prompt（简单直接） |
| 大量 skill | 仅注入 L1 metadata + 提供 `read_skill` 工具，模型按需调用获取 L2 body |

初始实现走"少量 skill 全量注入"路径，后续按需切到工具模式。

**Subagent 可见性**：Search/Crawl Agent 不加载 skill，仅 Lead Agent 可见。

### 9.6 前端

设置/个人页面中的 Skill 管理面板（独立于 conversation 流程）：
- Skill 列表（名称 + 描述 + 启用/禁用开关）
- 创建/编辑表单（name, description, content markdown 编辑器）
- 删除确认

### 9.7 涉及文件

**新增**:
- `src/db/models.py` — `Skill` 模型
- `src/repositories/skill_repo.py` — Skill CRUD
- `src/api/routers/skills.py` — API 端点
- `src/api/schemas/skill.py` — 请求/响应 schema
- `frontend/src/` — Skill 管理 UI 组件

**修改**:
- `src/core/context_manager.py` — `prepare_agent_context()` 增加 skill 查询和注入
- `src/agents/lead_agent.py` — `build_system_prompt()` 增加 `<available_skills>` section
- `src/api/main.py` — 注册 skills router
- `src/api/dependencies.py` — 注入 skill_repo

**依赖**: 仅依赖 Phase 4（认证，已完成）。独立于 Phase 7/8，可随时实施。

---

## Phase 10: 内网离线部署

**目标**: 实现"外网构建 + 内网运行"的标准化发布流程，支持无外网环境下的镜像交付和离线部署。适用场景：企业私有知识库、对接内网数据库做数据分析/报表等。

**前置依赖**: Phase 5/6（4-service 栈定型后统一改造，避免 compose 反复修改）。

**背景**: 当前 `docker-compose.yml` 是"源码构建部署"模式（service 定义包含 `build`，前端 API 地址通过构建参数写入，默认 Agent 模型指向公网服务）。外网环境可直接构建运行，但在无外网、无内网镜像仓的环境中，`docker compose up` 会遇到：构建依赖无法下载、前端回源地址不匹配、模型调用出网失败。需要把"构建时联网"与"运行时离线"彻底解耦。

**开发者模式**: Phase 5/6 完成后砍掉 SQLite 支持，只保留 PostgreSQL。本地开发采用"基础设施 Docker + 应用本地跑"模式：`docker-compose.dev.yml` 仅包含 PostgreSQL + Redis 两个 service，后端和前端本地直接运行（保留热重载和调试体验）。

### 10.1 应用配置改造 — 模型配置外部化

**目标**: Agent 使用的模型名、推理服务地址、API Key 全部通过环境变量注入，支持运行时从前端切换 Lead Agent 模型。

**设计决策**:
- **单 Provider**：所有模型共享一个推理服务地址（内网典型场景），不做多 Provider
- **手动声明可用模型**（不做自动发现）
- **env var default + per-message override**：管理员通过 env var 设默认模型，用户发消息时可从前端切换 Lead Agent 模型
- **公网模式不受影响**：`LLM_BASE_URL` 不设时走 litellm 默认路由（当前行为）

**新增配置项**（`config.py`，`ARTIFACTFLOW_` 前缀）:

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `LLM_BASE_URL` | 推理服务地址（空 = litellm 默认路由） | `""` |
| `LLM_API_KEY` | 推理服务 API Key | `""` |
| `LLM_AVAILABLE_MODELS` | 逗号分隔的可用模型 ID（空 = 用 `MODEL_CONFIGS` 预设） | `""` |
| `LEAD_MODEL` | Lead Agent 默认模型 | `"qwen3.5-plus"` |
| `WORKER_MODEL` | Search/Crawl Agent 默认模型 | `"qwen3.5-flash-no-thinking"` |

**两种运行模式**（由 `LLM_BASE_URL` 是否设置自动切换）：

| | 公网模式（当前行为） | 内网模式 |
|---|---|---|
| 可用模型来源 | `MODEL_CONFIGS` 预设 keys | `LLM_AVAILABLE_MODELS` 配置 |
| 模型路由 | litellm 按 provider 前缀路由 | 全部走 `LLM_BASE_URL` |
| API Key | 各 provider 独立 env var | 统一 `LLM_API_KEY` |

**改造链路**（从底向上穿透）:

1. **`models/llm.py`** — `create_llm()` 自动注入全局 Provider（调用方未指定 `base_url` 时）；`get_available_models()` 按模式返回不同来源；保留 `MODEL_CONFIGS` 不动（公网模式的 extra_params 仍需要）
2. **Agent 工厂** — `create_lead_agent(model=)` 参数化，默认值改为 `config.LEAD_MODEL`；Search/Crawl 同理用 `config.WORKER_MODEL`
3. **`graph.py`** — `create_multi_agent_graph(lead_model=)` 新增参数，传入 lead agent 工厂。Search/Crawl 不暴露 override（执行层模型由管理员统一配置）
4. **API 层** — `ChatRequest` 增加可选 `model` 字段；router 校验 model 在可用列表中后传入 graph；新增 `GET /api/v1/models` 端点返回可用模型列表 + 当前默认值
5. **前端** — Chat 输入区域增加模型选择下拉，页面加载时从 `/models` 获取列表，发送消息时附带选中的 model

**涉及文件**:

- **修改**: `config.py`, `models/llm.py`, `lead_agent.py`, `search_agent.py`, `crawl_agent.py`, `graph.py`, `schemas/chat.py`, `routers/chat.py`, `main.py`, `frontend/src/lib/api.ts`, `frontend/src/components/chat/`, `.env.example`
- **新增**: `routers/models.py`, `schemas/model.py`

**退出标准**:
- 内网模式：设置 `LLM_BASE_URL` + `LLM_AVAILABLE_MODELS` → agent 调用走指定推理服务
- 公网模式：不设 `LLM_BASE_URL` → 行为与改造前完全一致
- 前端可选模型、per-message override 生效
- `validate_config()` 在内网模式下校验默认模型在可用列表中

---

### 10.2 外网构建发布流程

**目标**: `scripts/release.sh` 一键完成版本化构建 → 冒烟验证 → 镜像导出 → sha256 校验。

**流程**: 版本号传入 → 构建 backend/frontend 双镜像（带 version label）→ 启动全栈跑健康检查 → `docker save` 导出四个 tar（含 postgres + redis-stack 基础设施镜像）→ 生成 `checksums.sha256`。

**前端 `NEXT_PUBLIC_API_URL` 处理**: 推荐每个部署环境单独构建前端镜像（方案 C）——前端镜像轻量（~50MB），`NEXT_PUBLIC_*` 是 Next.js 编译时变量，运行时替换都是 workaround。

**退出标准**:
- `release.sh` 一键完成全流程
- 导出的 tar 可在全新机器上 `docker load` 成功

---

### 10.3 内网 Compose + 配置模板

**目标**: `deploy/docker-compose.intranet.yml`（纯 `image`，无 `build`）+ `deploy/.env.intranet.example`（配置模板）。

**关键设计**:
- 四个 service：backend / frontend / postgres / redis-stack，全部仅 `image` 引用
- 健康检查链：redis/postgres healthy → backend healthy → frontend 启动
- 三个 named volume 持久化（backend_data / postgres_data / redis_data）
- DB 连接串在 compose 内拼接，敏感值（JWT secret、PG password、LLM key）从 `.env` 读取
- 端口可配（`BACKEND_PORT` / `FRONTEND_PORT`）

**`.env.intranet.example` 必填项**: VERSION、JWT_SECRET、POSTGRES_PASSWORD、LLM_BASE_URL、LLM_API_KEY、LLM_AVAILABLE_MODELS、LEAD_MODEL、WORKER_MODEL。

**退出标准**:
- 纯离线环境（镜像已 load）`docker compose up -d` 四个 service 全部 healthy
- 前端可访问且能正常登录

---

### 10.4 内网部署 SOP

**目标**: `deploy/deployment-guide.md`，运维人员无需理解技术栈即可完成部署。

**大纲**: 前置要求（Docker 24+ / 4C8G / OpenAI 兼容推理服务已部署）→ 镜像导入（sha256 校验 + docker load）→ 配置（复制 .env 模板 + 修改必填项）→ 启动 → 创建管理员 → 验证。附日常运维（日志 / 备份 / 升级）。

**退出标准**: 按手册操作可在全新机器上完成完整部署。

---

### 10.5 交付物清单

```
artifactflow-release-${VERSION}/
├── images/          # 四个 tar + checksums.sha256
├── compose/         # docker-compose.intranet.yml + .env.intranet.example
├── scripts/         # load-images.sh（docker load 一键脚本）
└── docs/            # deployment-guide.md
```

---

## 各 Phase 依赖关系

```
Phase 1 (核心 Bug)          ✅ 已完成
Phase 2 (安全加固)          ✅ 已完成
Phase 3 (数据质量)          ← 3.1/3.2 ✅, 3.3 ⏸️
Phase 4 (认证框架)          ✅ 已完成
Phase 5 (Redis + Alembic)    ← Phase 4 之后
  5.1 RETURNING 移除 + Alembic ← 无依赖
  5.2 Redis Checkpointer     ← 无依赖（可与 5.1 并行）
  5.3 Redis StreamManager    ← 可与 5.2 并行（共用 Redis 连接）
  5.4 缓存决策               ← 不做改动（保持 request-local）
  5.5 TaskManager 适配       ← 依赖 5.2（需要 Redis 连接）
Phase 6 (PostgreSQL)         ← 依赖 5.1（Alembic）
  6.1 引擎切换
  6.2 Schema 迁移             ← 依赖 6.1
  6.3 复合索引                ← 依赖 6.2
Phase 7A (文档上传)          ✅ 已完成
Phase 7B (结构化数据)        ← 依赖 7A ✅ + Phase 5/6（PostgreSQL 大字段 / 对象存储）
Phase 8 (编辑 Artifact)      ← 依赖 7A ✅（上传和编辑共享写接口模式）
Phase 9 (Skill 系统)         ← 仅依赖 Phase 4（认证），独立于 Phase 7/8，可随时实施
Phase 10 (内网离线部署)      ← 依赖 Phase 5/6（4-service 栈定型）
  10.1 模型配置外部化          ← 无外部依赖，可独立于 5/6 先行（仅改 LLM/Agent/API 层）
  10.2 外网构建发布流程        ← 依赖 10.1 + Phase 5/6
  10.3 内网 Compose            ← 依赖 10.2
  10.4 内网部署 SOP            ← 依赖 10.3
  10.5 交付物清单              ← 依赖 10.2 + 10.3 + 10.4
```

建议执行顺序: **Phase 4 ✅ → 7A ✅ → 5/6 → 10 → 7B → 8**。Phase 9 可随时排入开发。

关键路径: **5.1 ∥ 5.2/5.3 → 5.5 → 6.1 → 6.2 → 6.3 → 10.1 → 10.2 → 10.3**。10.1（模型配置外部化）可在 Phase 5/6 期间并行推进。

---

## 备注

- Phase 1-3 是纯修复，不引入新依赖，风险最低
- Phase 4 是第一个需要前端大改的阶段（登录页 + token 管理）
- Phase 5 优先 Redis（解决并发瓶颈）+ Alembic（为 Phase 6 铺路），5.1 和 5.2 可并行推进
- Phase 6 PostgreSQL 迁移依赖 5.1 的 Alembic 框架。DatabaseManager 不做方言适配 — 只认 `DATABASE_URL` + 通用连接池参数，切库改连接串即可
- Phase 7A ✅ 已完成（content_type 统一、文档转换、上传 API/UI、渲染策略修正、Prompt Review）
- Phase 7B 建议在 Phase 5/6 之后，因为 csv / json 原始文件存储可能需要 PostgreSQL 大字段或对象存储支持
- Phase 9 Skill 系统调研已完成，方案已确定（轻量独立 `skills` 表），仅依赖 Phase 4（已完成），可随时排入开发
- Phase 10 内网部署改造建议在 Phase 5/6 之后统一做，避免 compose 文件改两遍。10.1 模型配置外部化是最关键的子项，可提前讨论方案
- **数据迁移**: 系统处于开发阶段，所有 Phase（包括 5/6 数据库改造）均不需要考虑旧数据迁移，已有数据可丢弃
- **SQLite 退役**: Phase 6 中移除 SQLite 支持（Redis 已是必需依赖，本地开发必须跑 Docker，再多一个 PostgreSQL 容器成本为零）。测试统一跑 PostgreSQL
- concurrency.md 中已标记 ✅ 的项目（短事务、日志上下文、SSE Heartbeat 等）不在此计划中。TaskManager 多 worker 适配已纳入 5.5
- Phase 5/6 完成后需同步更新 `docs/architecture/concurrency.md`（演进路线、资源分层图）和 `CLAUDE.md`（命令、架构描述）
