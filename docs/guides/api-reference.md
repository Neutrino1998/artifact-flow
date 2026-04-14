# API 参考

> 五个路由组 + 统一鉴权 + SSE 订阅 — 面向集成方的紧凑端点手册。完整字段与 schema 见 `/openapi.json`（`ARTIFACTFLOW_DEBUG=true` 时 `/docs` 渲染 Swagger UI）。

## 通用约定

- **Base URL**：`/api/v1/{group}`，group ∈ `auth / chat / artifacts / stream / admin`
- **鉴权**：除 `/health/*` 与 `/auth/login` 外全部要求 `Authorization: Bearer <JWT>`；Admin 路由额外要求 `role=admin`
- **JSON**：请求与响应默认 `application/json`；文件上传用 `multipart/form-data`
- **时间**：所有 timestamp 用 ISO-8601 字符串
- **ID**：所有资源 ID 用字符串（非自增数字），避免跨存储后端的整型语义差异

### 状态码约定

| 码 | 含义 | 触发 |
|----|------|------|
| `200` | 成功 | 正常响应 |
| `202` | 异步接受 | `/chat/{id}/compact` 后台任务已排队 |
| `401` | 未鉴权 | 缺 token / token 失效 / 用户被禁用 |
| `403` | 权限不足 | 非 admin 访问 `/admin/*` 端点 |
| `404` | 资源不存在 | **也覆盖"跨用户访问"** — 见 Design Decision |
| `409` | 冲突 | Lease 冲突 / interrupt 已解决 / compaction 在跑 |
| `410` | 资源失效 | `active-stream` 指向的 stream 已过期 |
| `422` | 请求不合法 | 参数校验、文件过大、格式不支持 |
| `503` | 服务不可用 | Health ready 降级、Compaction 后台服务未启动 |

### 404-not-403 原则

跨用户访问他人资源（`GET /chat/{someone_elses_conv_id}` 等）一律返回 **404** 而非 403，不泄露资源存在性。实现上 Repository 查询即带 `user_id` 过滤，"没找到"与"不是你的"在同一代码路径。

---

## Auth

`/api/v1/auth` — 登录与用户管理（无自助注册）。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/login` | — | 用户名密码换 JWT |
| GET | `/me` | 用户 | 当前用户信息 |
| POST | `/users` | admin | 创建用户 |
| GET | `/users` | admin | 列出用户（`limit` 1-200, `offset`, `q` 搜索） |
| PUT | `/users/{user_id}` | admin | 更新 display_name / password / role / is_active |

**POST /login**

```http
POST /api/v1/auth/login
{"username": "alice", "password": "..."}

200 OK
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {"id": "...", "username": "alice", "display_name": "Alice", "role": "user"}
}
```

- 失败统一返回 **401**（用户不存在 / 密码错 / 账号禁用），不区分原因
- **无 `/refresh` 端点**：token 过期后客户端需重新 login

**POST /users**

- 字段：`username`, `password`, `display_name`, `role` ∈ `user` / `admin`
- `409` 用户名已存在；`400` 非法 role

---

## Chat 对话

`/api/v1/chat` — 所有对话、执行、消息树、permission 接续、compaction 都在这一组。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `` | 用户 | 发送消息、启动执行，返回 `stream_url` |
| GET | `` | 用户 | 列出自己的对话（`limit` 1-100, `offset`, `q`） |
| GET | `/{conv_id}` | 用户 | 对话详情含消息树 |
| DELETE | `/{conv_id}` | 用户 | 删除对话 |
| GET | `/{conv_id}/active-stream` | 用户 | 查询当前是否有运行中的 stream（用于重连） |
| POST | `/{conv_id}/inject` | 用户 | 运行中注入一条 user 消息 |
| POST | `/{conv_id}/cancel` | 用户 | 请求取消运行 |
| POST | `/{conv_id}/resume` | 用户 | Permission 审批后恢复运行 |
| POST | `/{conv_id}/compact` | 用户 | 手动触发对话压缩 |
| GET | `/{conv_id}/messages/{msg_id}/events` | 用户 | 单个 message 的事件链（可观测性） |

### POST `` — 发送消息

```http
POST /api/v1/chat
{
  "user_input": "...",
  "conversation_id": "..." | null,        // null → 新建对话
  "parent_message_id": "..." | null       // 可指定分支父节点
}

200 OK
{
  "conversation_id": "...",
  "message_id": "...",
  "stream_url": "/api/v1/stream/{message_id}"
}
```

- **`409 Conflict`**：该对话已有执行占用 lease — 客户端应先 `cancel` 或等前一次结束
- **`404`**：`conversation_id` 存在但不属于当前用户（见 404-not-403）
- 调用方拿到 `stream_url` 后必须立即 `GET`，否则 Transport 的 pending TTL 到期会清理

### POST `/{conv_id}/inject` — 运行中注入

```http
POST /api/v1/chat/{conv_id}/inject
{"content": "补充一条要求"}

200 OK
{"message_id": "...", "stream_url": "/api/v1/stream/{original_msg_id}"}
```

- 注入的消息**不会**新建 `Message` 行，仅作为 `queued_message` 事件持久化
- `409`：对话不在 interactive 状态（引擎已退出或未启动）
- `stream_url` 指向**原执行**的 stream — 客户端无需重新订阅

### POST `/{conv_id}/cancel`

```http
POST /api/v1/chat/{conv_id}/cancel
→ 200 {"message_id": "..."}
```

- 设置取消标志，引擎在下一检查点 emit `cancelled` 并终止；并非立即生效
- `409`：对话无运行中执行

### POST `/{conv_id}/resume` — Permission 接续

```http
POST /api/v1/chat/{conv_id}/resume
{"message_id": "...", "approved": true, "always_allow": false}

200 OK
{"stream_url": "/api/v1/stream/{message_id}"}
```

- `404`：message 或 interrupt 不存在
- `409`：interrupt 已解决（超时、被 cancel 唤醒、或已被处理）
- `stream_url` 与原 `POST /chat` 返回相同，继续订阅即可

### POST `/{conv_id}/compact`

- 触发后台压缩任务，`202` 表示已接受（`{"status": "accepted", "conversation_id": "..."}`）
- `409`：已有压缩在跑；`503`：未启用 CompactionManager

### GET `/{conv_id}/active-stream`

- 返回 `{"conversation_id", "message_id", "stream_url"}`
- `404`：当前无运行中执行
- `410`：有执行但对应 stream 已过 `EXECUTION_TIMEOUT` 失效

### GET `/{conv_id}/messages/{msg_id}/events`

- Query：`event_type`（可选过滤）
- 返回该消息的完整事件链（不含 `llm_chunk`，见 [../architecture/observability.md](../architecture/observability.md)）
- 用户侧可观测端点；Admin 版本见下方 `/admin/conversations/{id}/events`

---

## Artifacts

`/api/v1/artifacts` — 上传、读取、导出、版本管理。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/upload` | 用户 | 新建对话并上传首个 artifact |
| GET | `/{session_id}` | 用户 | 列出 session 下所有 artifacts |
| POST | `/{session_id}/upload` | 用户 | 向已有 session 追加 artifact |
| GET | `/{session_id}/{artifact_id}` | 用户 | 获取内容 + 版本列表 |
| GET | `/{session_id}/{artifact_id}/versions/{version}` | 用户 | 获取指定版本 |
| GET | `/{session_id}/{artifact_id}/export?format=docx` | 用户 | 导出为 DOCX |

### 上传约定

- `multipart/form-data`，字段名 `file`
- 大小上限：`config.MAX_UPLOAD_SIZE`（环境可配，默认见 [deployment.md](../deployment.md)）
- `422` 触发：超限、格式不支持
- 走 `create_from_upload` 路径绕过 write-back cache，直接 commit 到 DB（见 [../architecture/artifacts.md](../architecture/artifacts.md)）

### 读取的即时性

- `GET /{session_id}` 和 `GET /{session_id}/{artifact_id}` 会**合并 DB + 当前 engine 的内存缓存**，返回最新快照
- `GET .../versions/{version}` 与 `GET .../export` **只读 DB**：执行中未 flush 的中间版本返回 `404`

---

## Stream (SSE)

`/api/v1/stream` — 订阅某次执行的事件流。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/{stream_id}` | 用户 | SSE 长连接；`stream_id == message_id` |

**请求头：**

- `Authorization: Bearer <JWT>` —— 强制；不支持 query token
- `Last-Event-ID: <entry_id>` —— 可选；Redis transport 下实现续传，InMemory 模式忽略

**响应：**

- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`

**事件格式**（多行，空行分隔）：

```
event: tool_complete
id: 1710000003456-0
data: {"type":"tool_complete","timestamp":"...","agent":"search_agent","data":{...}}
```

心跳以 SSE comment 发送（`: ping\n\n`）；间隔 `SSE_PING_INTERVAL`（默认 15s）。

终止条件：收到 `complete / cancelled / error` 后连接关闭。

事件类型完整列表与 `data` 字段契约见 [../architecture/observability.md → 事件目录](../architecture/observability.md#事件目录)。Transport 细节与断线续传见 [../architecture/streaming.md](../architecture/streaming.md).

---

## Admin

`/api/v1/admin` — 全部端点 `role=admin`，非 admin 返回 **403**。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/conversations` | 全站对话列表 + 活跃标记 |
| GET | `/conversations/{conv_id}/events` | 按 message 分组的事件时间线 |

### GET `/conversations`

Query：`limit` (1-100, 默认 20), `offset`, `q` (title 搜索), `user_id` (按 owner 过滤)

响应：

```json
{
  "conversations": [
    {
      "id": "...", "title": "...", "user_id": "...", "user_display_name": "...",
      "message_count": 42, "is_active": true,
      "created_at": "...", "updated_at": "..."
    }
  ],
  "total": 120,
  "has_more": true
}
```

`is_active` 实时来自 RuntimeStore（见 [../architecture/observability.md → Admin API](../architecture/observability.md#admin-api)）。

### GET `/conversations/{conv_id}/events`

见 [../architecture/observability.md → Admin API](../architecture/observability.md#get-apiv1adminconversationsconv_idevents).

---

## Health

`src/api/main.py`，**无鉴权**（故意），供 LB / K8s 探针。

| 方法 | 路径 | 行为 |
|------|------|------|
| GET | `/health/live` | 始终 200 `{"status":"ok"}` |
| GET | `/health/ready` | DB + Redis 连通性检查；全通过 200，任一失败 503 |

详见 [../architecture/observability.md → 健康检查](../architecture/observability.md#健康检查).

---

## Design Decisions

### 为什么 `/chat` 是资源路径而非 `/conversations`

- `conversation` 是 DB 名词，但对外语义是"对话"而非"会话记录"；`/chat` 读起来即动作也即资源
- 保持 `/chat`、`/chat/{id}/inject`、`/chat/{id}/cancel` 的一致命名 — action 不单独用 `PATCH /conversations/{id}`
- 内部仍使用 `conversation_id` 字段名，命名空间与 URL 解耦

### 为什么没有 `/auth/register` / `/auth/refresh`

- 产品定位是团队内 SaaS，用户由 admin 主动创建；开放自助注册会放大滥用面
- JWT 直接用较长 `expires_in`（默认 86400s），过期重新 login — 省掉 refresh token 的存储与撤销复杂度
- 有强制失效需求可由 admin 通过 PUT `/users/{id}` 设 `is_active=false` 实现

### 为什么 Stream 用 `stream_id == message_id`

- 一次执行对应一个 message，ID 天然一对一，省去映射表
- 重连时客户端无需保存独立的 `stream_id`，只要记得上次的 `message_id`
- 权限校验天然走"这个 message 是不是你的"

### 为什么 Inject / Cancel / Resume 都走 POST `/chat/{id}/...`

- 都是对"运行中的执行"的副作用动作，资源定位都是 `conv_id`
- 副作用天然非幂等（inject 会排队、cancel 会设标志），POST 语义契合
- 统一的路径 prefix 让前端 api client 可以共享 conv 级别的 wrapper
