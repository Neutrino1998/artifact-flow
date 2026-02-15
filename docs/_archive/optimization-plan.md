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
- `src/core/repositories/` — 可能需要新增查询方法

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
- `src/core/repositories/` — 可能需要新增 count 查询

**改动**:
- Repository 层新增 `count_conversations()` 方法（`SELECT COUNT(*) FROM conversations`）
- Router 层并行执行 count 查询和分页查询，返回准确 total

### 3.2 Artifact created_at 返回真实时间 ✅

**问题**: `artifacts.py` 用 `datetime.now()` 代替真实创建时间。

**涉及文件**:
- `src/api/routers/artifacts.py` — 响应构造（~L54, ~L93）
- `src/core/artifact_manager.py` 或 `src/tools/implementations/artifact_ops.py` — ArtifactMemory 数据结构

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
- Phase 5（PostgreSQL 迁移）完成后再结合压测数据评估，收益/风险比更清晰

**后续触发条件（再开启本项）**:
- 有明确数据表明 graph 编译耗时显著影响 p95/p99 或吞吐
- 完成工具无状态化审计（至少 `artifact_ops`、`web_fetch`）
- 有可自动化回归覆盖 new/resume/streaming/并发双会话

---

## Phase 4: 认证框架

**目标**: 实现用户认证和租户隔离，所有 API 路由强制 `current_user`。

### 4.1 后端认证

**涉及文件**:
- `src/api/dependencies.py` — `get_current_user()` 改为真实实现
- `src/api/main.py` — 可能需要认证中间件
- `src/api/routers/chat.py` — 所有路由注入 `current_user`
- `src/api/routers/artifacts.py` — 所有路由注入 `current_user`
- `src/api/routers/stream.py` — SSE 连接认证
- `src/db/models.py` — `Conversation.user_id` 已预留，需要新增 User 模型或依赖外部 IdP
- `src/core/repositories/` — 所有查询增加 `user_id` 过滤条件

**改动**:
- 选择认证方案：JWT（自签发）或 OAuth2（接入外部 IdP）
- 实现 `get_current_user()` 从 `Authorization: Bearer <token>` 解析用户
- 所有 conversation/artifact 查询增加 `WHERE user_id = :current_user` 过滤
- SSE 端点支持 query param 传 token（因为 EventSource 不支持自定义 header，但我们用 fetch 所以可以用 header）
- resume 流程增加用户归属校验

### 4.2 前端认证

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

## Phase 5: PostgreSQL 迁移

**目标**: 主数据库从 SQLite 迁移到 PostgreSQL，为多用户/多 worker 打好基础。对应 concurrency.md Phase 2.5。

### 5.1 数据库引擎切换

**涉及文件**:
- `src/db/database.py` — `DatabaseManager` 引擎配置
- `src/config.py` — DATABASE_URL 配置
- `requirements.txt` — 增加 `asyncpg`（PostgreSQL 异步驱动）
- `docker-compose.yml` — 增加 PostgreSQL 服务

**改动**:
- `DatabaseManager` 移除 SQLite 专属配置（WAL PRAGMA 等），改为根据 URL scheme 自动适配
- 配置 PostgreSQL 连接池参数（`pool_size`, `max_overflow`）
- 更新 `DATABASE_URL` 默认值或环境变量

### 5.2 Schema 迁移

**涉及文件**:
- `src/db/models.py` — 检查 SQLite 特有的类型/约束是否兼容 PostgreSQL
- `src/db/migrations/` — Alembic 迁移脚本
- `src/core/repositories/` — 检查是否有 SQLite 特有 SQL

**改动**:
- 审查所有 model 字段类型的 PostgreSQL 兼容性（`JSON` → `JSONB`, `DateTime` → `TIMESTAMP WITH TIME ZONE` 等）
- 编写数据迁移脚本（从 SQLite 导出 → PostgreSQL 导入）
- 移除所有 SQLite PRAGMA 调用（`_configure_sqlite_wal` 改为条件执行或删除）

### 5.3 Checkpointer 迁移

**涉及文件**:
- `src/core/graph.py` — `create_async_sqlite_checkpointer` 改为 PostgreSQL
- `src/api/dependencies.py` — checkpointer 初始化
- `requirements.txt` — 增加 `langgraph-checkpoint-postgres`

**改动**:
- 使用 `langgraph-checkpoint-postgres` 的 `AsyncPostgresSaver` 替换 `AsyncSqliteSaver`
- Checkpointer 使用独立连接池（与主数据库分开），解决单连接瓶颈
- 配置 checkpoint TTL 自动过期

---

## Phase 6: Redis 引入（多 Worker 支持）

**目标**: 将进程内状态迁移到 Redis，支持多 worker 部署。对应 concurrency.md Phase 1 + Phase 2。

### 6.1 StreamManager 迁移到 Redis

**涉及文件**:
- `src/api/services/stream_manager.py` — 核心重构
- `src/api/dependencies.py` — Redis 连接初始化
- `src/config.py` — REDIS_URL 配置
- `requirements.txt` — 增加 `redis[hiredis]`
- `docker-compose.yml` — 增加 Redis 服务

**改动**:
- `asyncio.Queue` → Redis Pub/Sub 或 Redis Streams
- 事件缓冲：POST /chat push 事件到 Redis channel，GET /stream subscribe 消费
- TTL/清理逻辑迁移到 Redis key 过期机制
- 如果需要可靠投递（断线重连不丢消息），使用 Redis Streams + consumer group

### 6.2 ConversationManager 缓存迁移

**涉及文件**:
- `src/core/conversation_manager.py` — `_cache` dict → Redis Hash

**改动**:
- `_cache`（Python dict）替换为 Redis Hash
- 实现 cache invalidation 策略（TTL + 写穿）
- 或者简化：移除内存缓存层，全部走 DB（PostgreSQL 足够快），仅在热点路径用 Redis 缓存

### 6.3 Checkpointer 迁移到 Redis（可选）

**涉及文件**:
- `src/core/graph.py` — checkpointer 创建
- `src/api/dependencies.py` — checkpointer 初始化

**改动**:
- 如果 Phase 5.3 已迁移到 PostgreSQL checkpointer 且性能满足，此步可跳过
- 如需进一步优化：使用 `langgraph-checkpoint-redis` 的 `AsyncRedisSaver`
- Redis checkpointer 天然支持 TTL，checkpoint 数据自动过期

---

## Phase 7: 文件上传 → Artifact

**目标**: 支持用户上传文档，解析为 Artifact 供 Agent 操作。

### 7.1 后端上传 API

**涉及文件**:
- `src/api/routers/artifacts.py` — 新增 `POST /api/v1/artifacts/{session_id}/upload`
- `src/api/schemas/` — 新增上传请求/响应 schema
- `src/core/artifact_manager.py` — 新增 `create_from_upload()` 方法

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
- `src/core/artifact_manager.py` — 新增 `update_by_user()` 方法
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
Phase 1 (核心 Bug)     ✅ 已完成
Phase 2 (安全加固)     ✅ 已完成
Phase 3 (数据质量)     ← 无依赖，可与 Phase 1/2 并行
Phase 4 (认证框架)     ← 建议在 Phase 1 之后（ID 一致性修好后再加认证）
Phase 5 (PostgreSQL)   ← 建议在 Phase 4 之后（认证需要的 User 模型一起迁移）
Phase 6 (Redis)        ← 建议在 Phase 5 之后（基础设施逐步升级）
Phase 7 (文件上传)     ← 建议在 Phase 4 之后（需要认证知道上传者是谁）
Phase 8 (编辑 Artifact) ← 建议在 Phase 7 之后（上传和编辑共享写接口模式）
```

---

## 备注

- Phase 1-3 是纯修复，不引入新依赖，风险最低
- Phase 4 是第一个需要前端大改的阶段（登录页 + token 管理）
- Phase 5-6 是基础设施升级，需要 docker-compose 和部署配置变更
- Phase 7-8 是功能增强，可根据产品需求调整优先级
- concurrency.md 中已标记 ✅ 的项目（TaskManager、短事务、日志上下文、SSE Heartbeat 等）不在此计划中
