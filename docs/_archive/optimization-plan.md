# ArtifactFlow 分阶段优化计划

基于 code review 反馈和 `docs/architecture/concurrency.md` 演进路线整合。

---

## Phase 1: 核心流程 Bug 修复（ID 一致性 + Permission Resume） ✅ DONE

**目标**: 修复 permission resume 端到端链路断裂问题。当前 permission 确认流程无法正常工作。

> **完成于**: commit `c24fdb8` — 1.1~1.4 全部完成。Controller 接受外部传入 ID 并 fallback 自动生成，测试无需强制适配（向后兼容）。

### 1.1 统一 ID 生成源

**问题**: Router 层生成 `message_id`/`thread_id`，Controller 层又重新生成，导致前端持有的 ID 与实际执行 ID 不一致。

**涉及文件**:
- `src/api/routers/chat.py` — ID 生成点（~L65-72）
- `src/core/controller.py` — `_execute_new_message()`（~L214）和 `_stream_new_message()`（~L353）中的重复 ID 生成
- `src/core/controller.py` — `ExecutionController.stream_execute()` / `execute()` 方法签名

**改动**:
- Router 层生成 `message_id` + `thread_id` 作为唯一权威源
- Controller 的 `stream_execute()` / `execute()` 接受外部传入的 `message_id` + `thread_id` 参数
- 删除 Controller 内部 `_execute_new_message()` / `_stream_new_message()` 中的 ID 生成逻辑
- 测试适配：`tests/test_core_graph.py` 和 `tests/test_core_graph_stream.py` 中所有直接调用 `controller.execute(content=...)` / `controller.stream_execute(content=...)` 的地方需补传 `thread_id` + `message_id`；在 `TestEnvironment` 上新增 `generate_ids()` helper 封装 ID 生成，模拟 Router 层职责

### 1.2 前端 PermissionModal 从 streamStore 读取 ID

**问题**: `useSSE.ts` 的 `permission_request` handler 尝试从 SSE 事件 payload 读取 `thread_id`/`message_id`，但后端 `graph.py` 的 `permission_request` 事件根本不包含这两个字段，导致前端拿到空字符串。实际上，permission request 必然发生在当前活跃 stream 内，`streamStore` 中已通过 `startStream()` 持有正确的 `threadId`/`messageId`。

**涉及文件**:
- `frontend/src/hooks/useSSE.ts` — `PERMISSION_REQUEST` case（~L213-219）
- `frontend/src/components/layout/PermissionModal.tsx` — 已从 `permissionRequest` 对象读取 ID（~L23-24）

**改动**:
- `useSSE.ts` 的 `permission_request` handler 不再从 `data` 中读取 `thread_id`/`message_id`，改为存储 `toolName` + `params` 即可
- `PermissionModal` 的 `handleResponse()` 直接从 `streamStore` 读取 `threadId`/`messageId`（与读取 `streamUrl` 同源）
- `PermissionRequest` 类型定义中移除 `messageId`/`threadId` 字段
- 后端 `permission_request` 事件无需改动

### 1.3 前端处理 metadata 事件

**问题**: `useSSE.ts` 对 METADATA 事件直接 `break`，未提取真实执行 ID。配合 1.1 的 ID 统一后，metadata 事件中的 ID 可作为防御性校验。

**涉及文件**:
- `frontend/src/hooks/useSSE.ts` — METADATA case（~L78）

**改动**:
- METADATA 事件中提取 `conversation_id`、`message_id`、`thread_id`，与 `streamStore` 中已有值做一致性校验（dev 环境 console.warn 不一致情况）

### 1.4 resume 归属校验

**问题**: resume 端点完全信任客户端传入的 `thread_id`，未校验其与 `conversation_id` 的绑定关系。

**涉及文件**:
- `src/api/routers/chat.py` — `resume_execution()`（~L284-299）
- `src/db/models.py` — `Message` 模型已有 `thread_id` 和 `conversation_id` 字段
- `src/repositories/` — 可能需要新增查询方法

**改动**:
- resume 前查询 DB 校验 `message.thread_id == request.thread_id AND message.conversation_id == conv_id`
- 校验失败返回 403/404

---

## Phase 2: 安全加固 ✅ DONE

**目标**: 修复 SSRF 风险、持久化静默失败、生产环境配置问题。

> **完成于**: 2.1 web_fetch SSRF 防护（协议校验 + permission AUTO→CONFIRM）、2.2 持久化 fail fast（移除静默吞异常）、2.3 Docker healthcheck（/docs→/health）、2.4 错误信息脱敏（`_sanitize_error_event` 统一拦截所有 push 出口 + controller 不再向 graph_response 写入内部异常）。附带修复 permission 前端确认流程（interrupted COMPLETE 保留 stream 状态、streamStore 新增 conversationId/resumeStream、StreamManager 允许重建已关闭 stream）。

### 2.1 web_fetch SSRF 防护

**问题**: `web_fetch` 工具 permission 为 AUTO，且无 URL 校验，可访问内网/元数据地址。

**涉及文件**:
- `src/tools/implementations/web_fetch.py` — permission 定义（~L54）和 fetch 逻辑（~L246）

**改动**:
- 新增 URL 校验函数：拒绝私网 IP（10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x）、拒绝 `file://` 等非 HTTP 协议
- 对解析后的 IP 做二次校验（防 DNS rebinding：resolve 后检查 IP）
- Permission 从 `AUTO` 改为 `CONFIRM`（需用户确认）或至少对非白名单域名 CONFIRM

### 2.2 持久化异常 fail fast

**问题**: `conversation_manager.py` 三处 DB 写入失败被 `except Exception: logger.warning()` 吞掉。

**涉及文件**:
- `src/core/conversation_manager.py` — `_persist_conversation()`（~L170）、`_persist_message()`（~L300）、`_persist_response()`（~L337）

**改动**:
- 核心路径（persist_message、persist_response）的异常向上抛出，让 API 层返回错误
- 可降级路径（如 persist_conversation 的 DuplicateError）保留当前行为
- 在 `chat.py` 的 `execute_and_push()` 中捕获持久化异常，推送 error 事件到 stream

### 2.3 Docker 健康检查修复

**问题**: Healthcheck 依赖 `/docs`，生产环境 `DEBUG=False` 时 `/docs` 被禁用。

**涉及文件**:
- `src/api/main.py` — 需新增 `/health` 端点
- `Dockerfile` — healthcheck 命令（~L71）

**改动**:
- `main.py` 新增 `GET /health` 端点，返回 `{"status": "ok"}`
- Dockerfile healthcheck 改为 `curl -f http://localhost:8000/health`

### 2.4 错误信息脱敏

**问题**: Background task 异常通过 `str(e)` 直接推送前端，可能泄露内部路径/DB 信息。

**涉及文件**:
- `src/api/routers/chat.py` — error 事件推送（~L189, ~L398）

**改动**:
- 生产环境（`DEBUG=False`）：推送通用错误消息 "Internal server error"，详细信息仅写日志
- 开发环境（`DEBUG=True`）：保持当前行为，推送完整错误信息

---

## Phase 3: 数据质量改善 (3.1 ✅, 3.2 ✅, 3.3 ⏸️)

**目标**: 修复数据准确性问题。

> **3.1 & 3.2 完成于**: commit `14f44e6` — Repository 层新增 count 查询，Router 层返回准确 total；ArtifactMemory 增加 created_at 字段，使用 DB 真实时间。
> **补丁**: commit `925ce9f` — code review 修复：list_conversations 去掉 asyncio.gather 避免共享 AsyncSession；create_artifact 缓存构造传入 db_artifact.created_at。

### 3.1 分页 total 真实计数 ✅

**问题**: `total` 使用 `offset + len + (1 if has_more else 0)` 估算。

**涉及文件**:
- `src/api/routers/chat.py` — list conversations（~L202-207）
- `src/repositories/` — 可能需要新增 count 查询

**改动**:
- Repository 层新增 `count_conversations()` 方法（`SELECT COUNT(*) FROM conversations`）
- Router 层并行执行 count 查询和分页查询，返回准确 total

### 3.2 Artifact created_at 返回真实时间 ✅

**问题**: `artifacts.py` 用 `datetime.now()` 代替真实创建时间。

**涉及文件**:
- `src/api/routers/artifacts.py` — 响应构造（~L54, ~L93）
- `src/tools/implementations/artifact_ops.py` — ArtifactMemory 数据结构

**改动**:
- 检查 ArtifactMemory 是否持有 `created_at`，如果没有则从 DB 查询
- 如果 artifact 尚未持久化到 DB，使用内存中的创建时间（在 ArtifactMemory 中增加 `created_at` 字段）
- Router 层使用真实时间构造响应

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

**目标**: 实现用户认证和租户隔离，所有 API 路由强制 `current_user`。

> **完成于**: commit `8d367ae` — JWT 认证框架全面实现。后端：JWT 签发/验证、User 模型、get_current_user 依赖注入、所有路由 user_id 过滤、SSE 连接认证、resume 归属校验、admin 用户管理 API。前端：登录页、authStore（Zustand + localStorage）、AuthGuard 路由保护、API 请求自动附加 token、401 全局拦截跳转登录页、UserMenu + 管理员用户管理 UI。CLI：login/logout 命令、token 持久化。附带 code review 修复（`821f20a`）、用户管理 UI（`b573be1`）、logout 状态清理（`0a06643`）等。

### 4.1 后端认证 ✅

**涉及文件**:
- `src/api/dependencies.py` — `get_current_user()` 改为真实实现
- `src/api/main.py` — 可能需要认证中间件
- `src/api/routers/chat.py` — 所有路由注入 `current_user`
- `src/api/routers/artifacts.py` — 所有路由注入 `current_user`
- `src/api/routers/stream.py` — SSE 连接认证
- `src/db/models.py` — `Conversation.user_id` 已预留，需要新增 User 模型或依赖外部 IdP
- `src/repositories/` — 所有查询增加 `user_id` 过滤条件

**改动**:
- 选择认证方案：JWT（自签发）或 OAuth2（接入外部 IdP）
- 实现 `get_current_user()` 从 `Authorization: Bearer <token>` 解析用户
- 所有 conversation/artifact 查询增加 `WHERE user_id = :current_user` 过滤
- SSE 端点支持 query param 传 token（因为 EventSource 不支持自定义 header，但我们用 fetch 所以可以用 header）
- resume 流程增加用户归属校验

### 4.2 前端认证 ✅

**涉及文件**:
- `frontend/src/` — 新增登录页面组件
- `frontend/src/lib/api.ts` — 所有请求附加 Authorization header
- `frontend/src/lib/sse.ts` — SSE 连接附加 token
- `frontend/src/stores/` — 新增 authStore（token 存储、登录状态）
- `frontend/src/app/` — 路由保护（未登录跳转登录页）

**改动**:
- 新增登录/注册页面
- api.ts 封装请求拦截器，自动附加 token
- 401 响应全局拦截，跳转登录页
- token 持久化到 localStorage，刷新页面不丢失

---

## Phase 5: Redis 引入 + 数据库可移植性基线

**目标**: 解决最紧迫的并发瓶颈（checkpointer 单连接串行），建立数据库可移植性抽象层，为 Phase 6 PostgreSQL 迁移铺路。

> **执行顺序调整说明**: 原计划 Phase 5 PostgreSQL → Phase 6 Redis，但当前最明确的并发瓶颈是 checkpointer 单连接串行（`concurrency.md` L105-108），应优先解决。Redis 前置可更快获得并发收益，PostgreSQL 迁移改为 Phase 6。

### 5.1 数据库可移植性基线

**动机**: 在进行任何数据库迁移之前，确保 Repository 层不绑定特定 SQL 方言。未来切换数据库（PostgreSQL → MySQL/TDSQL）只需调整连接配置和迁移脚本，不改业务逻辑。

**涉及文件**:
- `src/repositories/base.py` — 新增方言适配工具方法
- `src/repositories/artifact_repo.py` — RETURNING 子句适配（L309-325，当前唯一的 RETURNING 用法）
- `src/db/database.py` — 提取方言检测能力（L86-88 `_is_sqlite()`），暴露给 Repository 层
- `src/db/migrations/` — Alembic 初始化

**改动**:

**5.1.1 RETURNING 子句可移植适配**

`artifact_repo.py:324` 的乐观锁更新使用了 `.returning()`，MySQL 不支持。设计可移植方案：

- `BaseRepository` 新增 `_supports_returning() -> bool` 方法，基于引擎方言判断
- `ArtifactRepository.update_artifact_content()` 改为：
  - 支持 RETURNING（PostgreSQL, SQLite 3.35+）→ 原子 `UPDATE...RETURNING`（当前行为）
  - 不支持 RETURNING（MySQL）→ fallback 为 `UPDATE` + `SELECT` 两步操作
- 两条路径在功能上等价，仅性能差异（一次 vs 两次 DB 调用）

**5.1.2 Alembic 迁移框架**

替换手写 SQL 迁移（`001_initial_schema.py` 使用原生 SQL 含 `AUTOINCREMENT`、`INSERT OR IGNORE` 等 SQLite 方言）：

- `alembic init` 初始化，`env.py` 配置 async engine
- 从当前 ORM models 生成初始迁移（`alembic revision --autogenerate`）
- 迁移脚本中使用 SQLAlchemy DDL API，不写方言专属 SQL
- 迁移执行策略：**部署前单次执行**（CLI 命令或 init container），不在应用启动时自动 `upgrade head`（多 worker 同时启动会导致并发迁移竞态）
- `DatabaseManager.initialize()` 改为 **schema version 校验**：启动时检查当前 DB 版本是否匹配预期，不匹配则 fail fast 并提示运行迁移命令
- 旧迁移脚本 `001_initial_schema.py` 保留，标记为 archived

**5.1.3 Repository 层方言审计**

确认所有 Repository 仅使用 SQLAlchemy 通用 ORM 能力（当前状态扫描结果）：
- ✅ 无原生 SQL（除 PRAGMA，已在 `database.py` 条件控制）
- ✅ 无 SQLite 特有函数（ROWID、typeof 等）
- ✅ JSON 类型使用 SQLAlchemy `JSON`（PostgreSQL 自动映射 JSONB）
- ✅ 所有查询通过 ORM 构造，无字符串拼接 SQL
- ⚠️ RETURNING 子句（artifact_repo.py L324）→ 5.1.1 已处理

**退出标准**:
- RETURNING 适配层有单元测试覆盖两条路径（RETURNING / fallback）
  - 注意：CI 仅 SQLite + PostgreSQL，两者都支持 RETURNING，fallback 路径不会被自然触发。需增加**强制 fallback 模式**的单测：mock `_supports_returning()` 返回 False，验证 UPDATE + SELECT 路径的正确性
- Alembic 迁移可在 SQLite 上正常执行
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

**目标**: 主数据库从 SQLite 迁移到 PostgreSQL，获得真正的多写者并发（MVCC）、复合索引性能、生产级连接池管理。

**前置依赖**: Phase 5.1（可移植性基线 + Alembic）已完成。

### 6.1 数据库引擎切换

**涉及文件**:
- `src/db/database.py` — `DatabaseManager` 多引擎适配
- `src/api/config.py` — `DATABASE_URL` 默认值更新
- `requirements.txt` — 新增 `asyncpg`
- `docker-compose.yml` — 新增 PostgreSQL 服务

**改动**:

**6.1.1 DatabaseManager 多引擎适配**

- 保留 `_is_sqlite()`，新增 `_get_dialect() -> str`（返回 `"sqlite"` / `"postgresql"` / `"mysql"` 等）
- SQLite 保留 WAL 配置（`_configure_sqlite_wal()` 条件调用，当前已实现 ✅），仍可用于开发/测试
- PostgreSQL 连接池参数：

```python
if self._get_dialect() == "postgresql":
    engine_kwargs.update({
        "pool_size": 10,          # 基础连接数
        "max_overflow": 20,       # 峰值溢出连接
        "pool_timeout": 30,       # 等待连接超时
        "pool_recycle": 1800,     # 连接最大存活 30 分钟
        "pool_pre_ping": True,    # 取连接前 ping 验活，防止使用已断开的连接
    })
```

- 可选：PostgreSQL 单语句超时 `connect_args={"server_settings": {"statement_timeout": "30000"}}`

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
- `scripts/` — 数据迁移脚本

**改动**:

**6.2.1 ORM 模型类型审查**

当前模型已基本兼容 PostgreSQL，需确认的点：
- `JSON` → SQLAlchemy 在 PostgreSQL 上自动映射为 `JSONB`（兼容 ✅，可显式标注提升可读性）
- `DateTime` → PostgreSQL `TIMESTAMP`（兼容 ✅）
- `String(N)` → `VARCHAR(N)`（兼容 ✅）
- `Text` → `TEXT`（兼容 ✅）
- `Integer` + `autoincrement=True`（`artifact_versions.id`）→ PostgreSQL `SERIAL`（SQLAlchemy 自动处理 ✅）

**6.2.2 数据迁移工具**

编写 `scripts/migrate_sqlite_to_pg.py`：
- 从 SQLite 按 FK 依赖顺序读取所有表数据
- 写入 PostgreSQL（批量 INSERT）
- 校验每张表的记录数一致性
- 支持 `--dry-run` 模式（只读不写，输出迁移计划）

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

注意：这些索引在 SQLite 上同样有效（SQLAlchemy 统一创建），不影响可移植性。

**退出标准**:
- PostgreSQL 上所有回归测试通过
- PostgreSQL 并发写入测试通过（模拟多请求同时写入 conversation/message/artifact）
- SQLite 模式仍可正常工作（开发/测试场景）
- 数据迁移脚本执行成功 + 记录数校验通过
- 复合索引在 EXPLAIN 中被正确使用

---

## 测试策略（贯穿 Phase 5 / Phase 6）

当前测试基座是内存 SQLite（`tests/conftest.py` L50-60），无法覆盖 PostgreSQL/Redis 行为差异。需要建立多数据库测试基础设施。

### 测试基础设施改造

**涉及文件**:
- `tests/conftest.py` — 多数据库 fixture
- `tests/integration/` — 新增集成测试目录
- CI 配置 — 多数据库 matrix

**改动**:

**环境变量驱动的数据库选择**:

```python
# tests/conftest.py
@pytest.fixture(scope="session")
def db_manager():
    db_url = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    manager = DatabaseManager(db_url)
    ...
```

**CI Matrix**:

```yaml
strategy:
  matrix:
    database: [sqlite, postgres]
    # MySQL 可选，后续按需加入
```

**Redis 集成测试**（`tests/integration/`）:
- `test_redis_checkpointer.py` — checkpoint CRUD / TTL 过期 / interrupt-resume
- `test_redis_stream_manager.py` — 事件推送/消费/断线重连/TTL 清理
- `test_redis_fault.py` — Redis 断连后 503 响应、Redis 恢复后自动重连
- `test_returning_adapter.py` — RETURNING 适配层测试：SQLite / PostgreSQL 行为一致性 + **mock `_supports_returning()=False` 强制 fallback 路径**（CI 的两种数据库都支持 RETURNING，不 mock 则 fallback 永远不被执行）

**并发测试增强**:
- 多请求并发写入 conversation/message/artifact（验证 PostgreSQL MVCC 优于 SQLite 单写者）
- 多 worker stream push/consume（验证 Redis Streams 跨进程投递）

---

## Phase 7: 文件上传 → Artifact

**目标**: 支持用户上传文档，解析为 Artifact 供 Agent 操作。

### 7.1 后端上传 API

**涉及文件**:
- `src/api/routers/artifacts.py` — 新增 `POST /api/v1/artifacts/{session_id}/upload`
- `src/api/schemas/` — 新增上传请求/响应 schema
- `src/tools/implementations/artifact_ops.py` — 新增 `create_from_upload()` 方法

**改动**:
- 新增上传端点，接受 `UploadFile`（multipart/form-data）
- 支持文件类型：`.txt`, `.md`, `.pdf`, `.csv`, `.json` 等
- 文件解析 pipeline：检测类型 → 提取文本 → 创建 Artifact（`metadata.source = "user_upload"`）
- 文件大小限制 + 类型白名单校验
- PDF 解析可复用 `web_fetch` 中的 PDF 处理逻辑（如有）

### 7.2 前端上传 UI

**涉及文件**:
- `frontend/src/components/chat/` — 已有上传按钮（`d122b01` commit 提到 "add upload file button"）
- `frontend/src/lib/api.ts` — 新增上传 API 调用
- `frontend/src/stores/artifactStore.ts` — 上传状态管理

**改动**:
- 连接已有的上传按钮到实际上传逻辑
- 上传进度展示（进度条或 spinner）
- 上传完成后自动刷新 artifact 列表
- 拖拽上传支持（可选）

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
- 更新时创建新 version 记录（`update_type = "user_edit"`）
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

## 各 Phase 依赖关系

```
Phase 1 (核心 Bug)          ✅ 已完成
Phase 2 (安全加固)          ✅ 已完成
Phase 3 (数据质量)          ← 3.1/3.2 ✅, 3.3 ⏸️
Phase 4 (认证框架)          ✅ 已完成
Phase 5 (Redis + 可移植性)   ← Phase 4 之后
  5.1 可移植性基线            ← 无依赖
  5.2 Redis Checkpointer     ← 无依赖（可与 5.1 并行）
  5.3 Redis StreamManager    ← 可与 5.2 并行（共用 Redis 连接）
  5.4 缓存决策               ← 不做改动（保持 request-local）
  5.5 TaskManager 适配       ← 依赖 5.2（需要 Redis 连接）
Phase 6 (PostgreSQL)         ← 依赖 5.1（可移植性基线 + Alembic）
  6.1 引擎切换
  6.2 Schema 迁移             ← 依赖 6.1
  6.3 复合索引                ← 依赖 6.2
Phase 7 (文件上传)           ← Phase 4 之后（需要认证知道上传者是谁）
Phase 8 (编辑 Artifact)      ← Phase 7 之后（上传和编辑共享写接口模式）
```

关键路径: **5.1 ∥ 5.2/5.3 → 5.5 → 6.1 → 6.2 → 6.3**（5.1 与 5.2 可并行）。5.2 完成后即可获得最大的并发性能提升（checkpointer 瓶颈解除）。

---

## 备注

- Phase 1-3 是纯修复，不引入新依赖，风险最低
- Phase 4 是第一个需要前端大改的阶段（登录页 + token 管理）
- Phase 5 优先 Redis（解决并发瓶颈）+ 可移植性基线（为 Phase 6 铺路），5.1 和 5.2 可并行推进
- Phase 6 PostgreSQL 迁移依赖 5.1 的 Alembic 框架和方言适配层
- Phase 7-8 是功能增强，可根据产品需求调整优先级
- 数据库可移植性 CI 范围：当前只跑 SQLite + PostgreSQL；MySQL/TDSQL 等有实际切库需求时再加入 matrix
- concurrency.md 中已标记 ✅ 的项目（短事务、日志上下文、SSE Heartbeat 等）不在此计划中。TaskManager 多 worker 适配已纳入 5.5
- Phase 5/6 完成后需同步更新 `docs/architecture/concurrency.md`（演进路线、资源分层图）和 `CLAUDE.md`（命令、架构描述）
