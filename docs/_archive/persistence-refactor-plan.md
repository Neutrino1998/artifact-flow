# 持久化改造计划 — Redis 多 Worker + PostgreSQL

> 基于 optimization-plan.md Phase 5/6 重新整理。
> 前置变更：LangGraph 已移除（Pi-style engine）、TaskManager 已拆分为 ExecutionRunner + RuntimeStore、StreamTransport Protocol 已抽象。

---

## 与旧计划（optimization-plan.md P5/P6）的差异

| 旧计划项 | 当前状态 | 本计划处理 |
|----------|---------|-----------|
| 5.1 RETURNING 移除 | ✅ 已完成（`.returning()` 已不存在） | 仅保留 Alembic 部分 |
| 5.2 Redis Checkpointer | ❌ **完全作废** — LangGraph 已移除，无 checkpointer | 删除 |
| 5.3 StreamManager → Redis Streams | Protocol 已抽象（`StreamTransport`） | → Phase 2 |
| 5.4 Manager 缓存决策 | 不变（request-local，不迁移 Redis） | 沿用 |
| 5.5 TaskManager 多 Worker | TaskManager 已不存在，拆为 ExecutionRunner + RuntimeStore | → Phase 1（核心改造） |
| 6.1-6.3 PostgreSQL | 无变化 | → Phase 3 |

**最大的变化**：LangGraph 移除后，Redis 不再是"解决 checkpointer 串行瓶颈"的角色，而是纯粹为多 Worker 部署服务。这意味着 Redis 的引入可以和多 Worker 适配一步到位，不需要分两轮做。

---

## 架构现状

```
全局单例 (app lifespan)                  请求级实例 (per HTTP request)
├─ DatabaseManager (SQLite, 连接池+WAL)   ├─ AsyncSession
├─ StreamTransport (InMemory Queue)       ├─ ConversationManager/Repo
├─ ExecutionRunner (asyncio.Task 调度)     ├─ ArtifactManager/Repo
│   └─ RuntimeStore (InMemory dict×5)     ├─ ExecutionController
├─ agents config (只读)                   └─ Repositories
└─ tools (只读)
```

**单进程限制清单**：

| 组件 | 限制 | 跨进程影响 |
|------|------|-----------|
| `InMemoryRuntimeStore` | 5 个 dict，进程内可见 | Worker A 的 lease/interrupt 对 Worker B 不可见 |
| `InterruptState.event` | `asyncio.Event`，进程内 await | `/resume` 只能唤醒同进程的 engine |
| `StreamManager` | `asyncio.Queue`，进程内消费 | Worker A push 的事件 Worker B 的 SSE 读不到 |
| `ExecutionRunner._tasks` | `dict[str, asyncio.Task]`，进程内 | 永远只能管理本 Worker 的任务 |
| `DatabaseManager` (SQLite) | 单写者，WAL 并发有限 | 多进程写入锁竞争 |

---

## Phase 1：Redis RuntimeStore — 多 Worker 一步到位

> 目标：`RuntimeStore` 从 InMemory 切到 Redis，解决 lease/interrupt/cancel/inject 的跨 Worker 可见性。同时解决 reviewer 指出的两个 P1 问题（sync→async Protocol、InterruptState 跨进程唤醒）。

### 1.1 Redis 基础设施

```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine          # 不再需要 Redis Stack（无 checkpointer）
  ports:
    - "6379:6379"
  volumes:
    - redis_data:/data
  command: >
    redis-server
    --appendonly yes
    --appendfsync everysec
    --save 900 1
    --maxmemory 256mb
    --maxmemory-policy noeviction
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 10s
    timeout: 5s
    retries: 3
```

与旧计划的区别：用 `redis:7-alpine` 而非 `redis/redis-stack`。LangGraph checkpointer 需要 RedisJSON + RediSearch 模块，但我们不再需要 checkpointer，纯 Redis 足矣。镜像从 ~300MB 降到 ~30MB。

配置项（`config.py`）：
- `REDIS_URL: str = "redis://localhost:6379"`

### 1.2 RuntimeStore Protocol 升级为 async

**Reviewer P1 要点**：当前 Protocol 全 sync，Redis 客户端（`redis.asyncio`）需要 `await`。

改动范围：

**`src/api/services/runtime_store.py`** — Protocol 方法全部加 `async`：

```python
@runtime_checkable
class RuntimeStore(Protocol):
    async def try_acquire_lease(self, conversation_id: str, message_id: str) -> Optional[str]: ...
    async def release_lease(self, conversation_id: str) -> None: ...
    async def get_leased_message_id(self, conversation_id: str) -> Optional[str]: ...

    async def mark_engine_interactive(self, conversation_id: str, message_id: str) -> None: ...
    async def clear_engine_interactive(self, conversation_id: str) -> None: ...
    async def get_interactive_message_id(self, conversation_id: str) -> Optional[str]: ...

    async def create_interrupt(self, message_id: str, data: Dict[str, Any]) -> None: ...
    async def resolve_interrupt(self, message_id: str, resume_data: Dict[str, Any]) -> Literal["resolved", "not_found", "already_resolved"]: ...
    async def get_interrupt_data(self, message_id: str) -> Optional[Dict[str, Any]]: ...
    async def wait_for_resume(self, message_id: str, timeout: float) -> Optional[Dict[str, Any]]: ...

    async def request_cancel(self, message_id: str) -> None: ...
    async def is_cancelled(self, message_id: str) -> bool: ...

    async def inject_message(self, message_id: str, content: str) -> None: ...
    async def drain_messages(self, message_id: str) -> List[str]: ...

    async def cleanup_execution(self, message_id: str) -> None: ...
    async def shutdown_cleanup(self) -> None: ...
```

**`InMemoryRuntimeStore`** — 同步实现加 `async` 关键字（dict 操作本身不阻塞，加 async 只是满足协议）。

**调用点适配**（机械改动，所有 `store.xxx()` → `await store.xxx()`）：
- `src/api/routers/chat.py` — send_message, inject, cancel, resume
- `src/api/routers/stream.py` — resolve_interrupt
- `src/core/engine.py` — EngineHooks 回调签名改 async
- `src/core/controller.py` — on_engine_exit 改 async
- `src/api/services/execution_runner.py` — cleanup_execution, shutdown_cleanup

### 1.3 Interrupt 跨进程唤醒（解决 Reviewer P2）

**问题核心**：engine 通过 `await interrupt.event.wait()` 阻塞，`/resume` 通过 `interrupt.event.set()` 唤醒。这个 `asyncio.Event` 是进程内对象，跨 Worker 无法传递。

**解决方案**：将 interrupt 拆成两层——Redis 存储状态 + 本地 Event 桥接唤醒。

```
Engine                    Redis                      /resume (任意 Worker)
  │                         │                              │
  ├─ create_interrupt ──►   SET interrupt:{msg_id}         │
  │                         {data: ..., status: pending}   │
  │                         │                              │
  ├─ wait_for_resume ──►    SUBSCRIBE interrupt:{msg_id}   │
  │   (block on local       │                              │
  │    asyncio.Event)       │                              │
  │                         │        resolve_interrupt ◄───┤
  │                         │        SET status=resolved    │
  │                         │        PUBLISH interrupt:{msg_id}
  │                         │              │
  │   ◄── on_message ──────────────────────┘
  │   local_event.set()     │
  │                         │
  ├─ read resume_data ──►   GET interrupt:{msg_id}
```

**Protocol 变化**：

旧接口：
```python
def create_interrupt(self, message_id, data) -> InterruptState  # 返回 InterruptState
# engine 直接 await interrupt.event.wait()
```

新接口：
```python
async def create_interrupt(self, message_id, data) -> None          # 只写入状态
async def wait_for_resume(self, message_id, timeout) -> Optional[Dict]  # 阻塞等待，返回 resume_data 或 None(超时)
async def resolve_interrupt(self, message_id, resume_data) -> ...   # 写入 resume_data + 通知
async def get_interrupt_data(self, message_id) -> Optional[Dict]    # 读取 interrupt_data（路由层用）
```

**关键设计**：`wait_for_resume` 内部机制因实现而异：
- `InMemoryRuntimeStore`：内部维护 `asyncio.Event`，`wait_for_resume` 就是 `await event.wait()`
- `RedisRuntimeStore`：`SUBSCRIBE interrupt:{msg_id}` + 本地 `asyncio.Event` 桥接。subscribe 回调中 set 本地 event

**Engine 改动**（`src/core/engine.py`）：

```python
# 旧：
interrupt = hooks.create_interrupt(message_id, interrupt_data)
await asyncio.wait_for(interrupt.event.wait(), timeout=config.PERMISSION_TIMEOUT)
resume_data = interrupt.resume_data

# 新：
await hooks.create_interrupt(message_id, interrupt_data)
resume_data = await hooks.wait_for_resume(message_id, timeout=config.PERMISSION_TIMEOUT)
# resume_data is None → timeout (treated as deny)
```

**EngineHooks 签名更新**：

```python
@dataclass
class EngineHooks:
    check_cancelled: Callable[[str], Awaitable[bool]]
    create_interrupt: Callable[[str, Dict[str, Any]], Awaitable[None]]
    wait_for_resume: Callable[[str, float], Awaitable[Optional[Dict[str, Any]]]]
    drain_messages: Callable[[str], Awaitable[List[str]]]
```

`InterruptState` dataclass 可以从 engine.py 中移除（或降级为 InMemoryRuntimeStore 的内部实现细节）。

### 1.4 RedisRuntimeStore 实现

**`src/api/services/redis_runtime_store.py`**：

```python
class RedisRuntimeStore:
    def __init__(self, redis: redis.asyncio.Redis):
        self._redis = redis
        self._local_events: dict[str, asyncio.Event] = {}  # 本地 interrupt 桥接
        self._subscriber_tasks: dict[str, asyncio.Task] = {}
```

**Redis Key 设计**：

| Key | 类型 | TTL | 用途 |
|-----|------|-----|------|
| `lease:{conv_id}` | STRING (msg_id) | `STREAM_TIMEOUT` | conversation lease |
| `interactive:{conv_id}` | STRING (msg_id) | `STREAM_TIMEOUT` | engine interactive 标记 |
| `interrupt:{msg_id}` | HASH {data, status, resume_data} | `PERMISSION_TIMEOUT + 60` | interrupt 状态 |
| `cancel:{msg_id}` | STRING "1" | `STREAM_TIMEOUT` | 取消标记 |
| `queue:{msg_id}` | LIST | `STREAM_TIMEOUT` | 消息注入队列 |

**关键方法实现**：

- `try_acquire_lease` → `SET lease:{conv_id} {msg_id} NX EX {ttl}`，原子操作天然防重
- `release_lease` → `DEL lease:{conv_id}`（需 compare-and-del，防误删其他 worker 的 lease）
- `is_cancelled` → `EXISTS cancel:{msg_id}`
- `inject_message` → `RPUSH queue:{msg_id} {content}`
- `drain_messages` → Lua 脚本：`LRANGE + DEL` 原子取出全部消息
- `create_interrupt` → `HSET interrupt:{msg_id} data {json} status pending`
- `resolve_interrupt` → Lua 脚本：检查 status=pending → 设 resume_data + status=resolved → `PUBLISH interrupt:{msg_id}`
- `wait_for_resume` → `SUBSCRIBE interrupt:{msg_id}` + 本地 `asyncio.Event`，超时返回 None
- `cleanup_execution` → pipeline DEL 所有相关 key

**Lua 脚本**（原子操作）：

```lua
-- compare-and-del（lease 释放、interactive 清除）
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end

-- drain-all（消息队列原子取出）
local msgs = redis.call("LRANGE", KEYS[1], 0, -1)
redis.call("DEL", KEYS[1])
return msgs

-- resolve-interrupt（原子状态转换 + 发布通知）
local status = redis.call("HGET", KEYS[1], "status")
if status == false then return "not_found" end
if status ~= "pending" then return "already_resolved" end
redis.call("HSET", KEYS[1], "status", "resolved", "resume_data", ARGV[1])
redis.call("PUBLISH", KEYS[1], "resolved")
return "resolved"
```

### 1.5 ExecutionRunner 适配

`ExecutionRunner` 保持为**本地调度器**（每个 Worker 一个实例），不做分布式化：

- `_tasks: dict[str, asyncio.Task]` — 永远只管本 Worker 的任务
- `_semaphore` — per-Worker 并发上限
- 分布式防重由 Redis lease 保证（`try_acquire_lease` 的 `SET NX` 天然跨 Worker 互斥）
- `submit()` 的 finally 中 `await self.store.cleanup_execution(task_id)` 已是 async

不需要全局 Semaphore（旧计划 5.5.2 继续 defer，理由同旧计划：per-Worker 限流已足够）。

### 1.6 故障处理

| 时机 | Redis 不可用的行为 |
|------|-------------------|
| 启动时 | `init_globals()` 中 `redis.ping()` 失败 → 应用启动失败（fail fast） |
| 请求时 | `try_acquire_lease` 抛 `ConnectionError` → router 层返回 503 |
| 执行中 | `is_cancelled` / `drain_messages` 失败 → engine 视为"无取消/无消息"，继续执行，日志 warning |
| interrupt | `wait_for_resume` 中 subscribe 断连 → 超时处理（视为 deny），与现有行为一致 |

### 1.7 涉及文件

| 文件 | 操作 |
|------|------|
| `docker-compose.yml` | **改** — 新增 Redis 服务 |
| `src/config.py` | **改** — 新增 `REDIS_URL` |
| `src/api/services/runtime_store.py` | **改** — Protocol async 化 + InMemory 适配 |
| `src/api/services/redis_runtime_store.py` | **新建** — Redis 实现 |
| `src/core/engine.py` | **改** — InterruptState 解耦、EngineHooks async 化、wait_for_resume |
| `src/core/controller.py` | **改** — on_engine_exit async、hooks 构建适配 |
| `src/api/routers/chat.py` | **改** — await store 调用 |
| `src/api/routers/stream.py` | **改** — await store 调用 |
| `src/api/services/execution_runner.py` | **改** — await store 调用 |
| `src/api/dependencies.py` | **改** — Redis 连接初始化、注入 RedisRuntimeStore |
| `tests/test_runtime_store.py` | **改** — async 适配 |
| `tests/test_execution_runner.py` | **改** — async 适配 |
| `tests/test_engine_execution.py` | **改** — hooks async 适配 |
| `tests/integration/test_redis_runtime_store.py` | **新建** — Redis 集成测试 |

### 1.8 退出标准

- [ ] 单 Worker 部署行为与改造前完全一致（InMemoryRuntimeStore 仍可用于开发/测试）
- [ ] 同一 conversation 并发 POST /chat 到不同 Worker → 第二个被拒绝（409）
- [ ] Worker A 启动的执行，Worker B 可以 inject/cancel/resume
- [ ] Permission interrupt 跨 Worker：Worker A 运行 engine → Worker B 收到 /resume → engine 唤醒
- [ ] Worker 崩溃后 lease TTL 过期 → 新请求可正常提交
- [ ] Redis 不可用 → 启动 fail fast / 请求 503 / 执行中 graceful degrade
- [ ] 现有回归测试全部通过

---

## Phase 2：Redis StreamTransport — 跨 Worker 事件推送

> 目标：`StreamTransport` 从 InMemory Queue 切到 Redis Streams，解决 POST /chat 和 GET /stream 可能落在不同 Worker 的问题。

### 2.1 为什么选 Redis Streams

与旧计划 5.3 相同，此处不重复。核心理由：Pub/Sub 断线丢消息，Streams 持久化 + 断点重放。

### 2.2 RedisStreamTransport 实现

**`src/api/services/redis_stream_transport.py`**：

```python
class RedisStreamTransport:
    """基于 Redis Streams 的 StreamTransport 实现"""

    def __init__(self, redis: redis.asyncio.Redis, ttl_seconds: int = 30):
        self._redis = redis
        self.ttl_seconds = ttl_seconds
```

**Redis Key 设计**：

| Key | 类型 | TTL | 用途 |
|-----|------|-----|------|
| `stream:{msg_id}` | STREAM | `STREAM_TTL` | 事件流 |
| `stream_meta:{msg_id}` | HASH {owner, status} | `STREAM_TTL` | stream 元数据 |

**方法映射**：

| StreamTransport 方法 | Redis 操作 |
|---------------------|-----------|
| `create_stream` | `HSET stream_meta:{id} owner {uid} status pending` + `EXPIRE` |
| `push_event` | `XADD stream:{id} MAXLEN ~1000 * type {t} data {json}` |
| `consume_events` | `XREAD BLOCK {heartbeat_interval} STREAMS stream:{id} {last_id}` |
| `close_stream` | `HSET stream_meta:{id} status closed` |
| `get_stream_status` | `HGET stream_meta:{id} status` |

**consume_events** 内部逻辑：
1. 校验 owner（`HGET stream_meta:{id} owner`）
2. 取消 TTL（前端已连接）
3. 循环 `XREAD BLOCK`，超时时 yield `__ping__`
4. 终结事件后 `EXPIRE`（延迟清理）

### 2.3 SSE 断线重连

与旧计划 5.3.3 相同：

- `consume_events` 新增 `last_event_id` 参数
- SSE 响应附带 `id:` 字段（Redis Stream entry ID）
- `src/api/routers/stream.py` 读取 `Last-Event-ID` header
- 前端 `lib/sse.ts` 手动维护 `last_event_id`（非 EventSource，无自动重连）

### 2.4 涉及文件

| 文件 | 操作 |
|------|------|
| `src/api/services/redis_stream_transport.py` | **新建** |
| `src/api/services/stream_transport.py` | **改** — consume_events 增加 last_event_id 参数 |
| `src/api/services/stream_manager.py` | **改** — 适配新参数（InMemory 实现忽略 last_event_id） |
| `src/api/routers/stream.py` | **改** — 读取 Last-Event-ID + SSE id 字段 |
| `src/api/routers/chat.py` | **改** — _run_and_push 适配（push_event 的 entry ID 透传） |
| `src/api/dependencies.py` | **改** — 注入 RedisStreamTransport |
| `src/api/utils/sse.py` | **改** — SSE event 增加 id 字段 |
| `frontend/src/lib/sse.ts` | **改** — 维护 last_event_id，断线重连携带 |
| `tests/integration/test_redis_stream_transport.py` | **新建** |

### 2.5 退出标准

- [ ] Worker A push 事件 → Worker B 的 SSE 连接可消费
- [ ] 断线重连：消费中断 → 用 last_event_id 重连 → 不丢消息
- [ ] TTL 过期后 Stream key 自动删除
- [ ] 所有权隔离：非 owner 消费被拒绝（`StreamNotFoundError`）
- [ ] Redis 故障：SSE 连接建立时 503 / 流中断时 SSE error 事件
- [ ] 现有回归测试全部通过

---

## Phase 3：PostgreSQL 迁移

> 与旧计划 Phase 6 基本一致，此处仅列出调整点。

### 3.1 Alembic 迁移框架

当前状态：手写 SQL 迁移（`001_initial_schema.py`，含 SQLite 方言 `AUTOINCREMENT`、`INSERT OR IGNORE`）。

改动与旧计划 5.1.2 一致：
- `alembic init`，`env.py` 配置 async engine
- 从 ORM models autogenerate 初始迁移
- 迁移执行策略：部署前单次执行，不在启动时自动 `upgrade head`
- `DatabaseManager.initialize()` 改为 schema version 校验
- 删除 `001_initial_schema.py`

### 3.2 DatabaseManager 简化 + 引擎切换

与旧计划 6.1.1 一致：
- 移除 `_is_sqlite()`、`_configure_sqlite_wal()`、PRAGMA 等 SQLite 专属代码
- 连接池参数外部化：`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_TIMEOUT`、`DB_POOL_RECYCLE`
- `DATABASE_URL` 默认值改为 PostgreSQL

### 3.3 Docker PostgreSQL 服务

与旧计划 6.1.2 一致。

### 3.4 复合索引

与旧计划 6.3 一致：
- `ix_conversations_user_updated` — `(user_id, updated_at)`
- `ix_messages_conv_created` — `(conversation_id, created_at)`

### 3.5 退出标准

- [ ] PostgreSQL 上全部回归测试通过
- [ ] 并发写入测试通过（MVCC）
- [ ] SQLite 专属代码已全部移除
- [ ] `/health` 端点检查 PostgreSQL + Redis 连通性

---

## 贯穿策略

### Manager 缓存决策（不变）

与旧计划 5.4 一致：`ConversationManager._cache` 和 `ArtifactManager._cache` 保持 request-local 内存缓存，不迁移 Redis。

### 测试策略

**单元测试**：继续用 `InMemoryRuntimeStore` + `StreamManager`（内存实现），无需 Redis。保证核心逻辑的快速反馈。

**集成测试**（`tests/integration/`）：

| 文件 | 覆盖 |
|------|------|
| `test_redis_runtime_store.py` | lease 跨 Worker 互斥、interrupt pub/sub 唤醒、TTL 过期清理 |
| `test_redis_stream_transport.py` | 跨进程 push/consume、断线重连、TTL |
| `test_redis_fault.py` | 连接中断 503、恢复后自动重连 |

**CI 基础设施**：`docker-compose.test.yml` 包含 Redis + PostgreSQL service container。

### 开发者体验

Phase 3 完成后砍掉 SQLite（与旧计划一致）。本地开发：
- `docker-compose.dev.yml` 包含 PostgreSQL + Redis
- 后端和前端本地直接运行（保留热重载）
- 环境变量 `REDIS_URL` 不设 → fallback 到 `InMemoryRuntimeStore`（便于快速原型，不推荐生产）

---

## 依赖关系与执行顺序

```
Phase 1: Redis RuntimeStore（多 Worker 核心）
  1.1 Redis 基础设施
  1.2 Protocol async 化（可先行，不依赖 Redis）
  1.3 Interrupt 跨进程唤醒设计
  1.4 RedisRuntimeStore 实现
  1.5 ExecutionRunner 适配
  1.6 故障处理
      │
      ▼
Phase 2: Redis StreamTransport（跨 Worker 事件）
  2.2 RedisStreamTransport 实现
  2.3 SSE 断线重连
      │
      ▼ （可与 Phase 2 并行）
Phase 3: PostgreSQL 迁移
  3.1 Alembic
  3.2 DatabaseManager 简化
  3.3 Docker PostgreSQL
  3.4 复合索引
```

**推荐拆分**：

Phase 1.2（Protocol async 化）可以先行，是纯机械改动且不引入新依赖。完成后 InMemoryRuntimeStore 仍然工作，但所有调用点已为 Redis 做好准备。

Phase 1.3-1.4 和 Phase 2 可并行开发（共用 Redis 连接）。

Phase 3 仅依赖 Alembic，与 Phase 1/2 无直接依赖，可并行推进。

---

## 验证矩阵

| 场景 | 单 Worker (InMemory) | 多 Worker (Redis) |
|------|---------------------|-------------------|
| POST /chat 并发保护 | dict lease ✅ | `SET NX` lease ✅ |
| inject/cancel 路由 | dict 查询 ✅ | `GET` 查询 ✅ |
| permission interrupt | asyncio.Event ✅ | pub/sub + local Event ✅ |
| SSE 事件投递 | asyncio.Queue ✅ | Redis Streams ✅ |
| 断线重连 | ❌ 无 | last_event_id ✅ |
| Worker 崩溃恢复 | N/A | TTL 自动释放 ✅ |
| DB 并发写入 | SQLite WAL (受限) | PostgreSQL MVCC ✅ |
