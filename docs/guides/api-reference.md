# API 参考

> 六个路由组 + 统一鉴权 + SSE 订阅 — 面向集成方的紧凑端点手册。完整字段与 schema 见 `/openapi.json`（`ARTIFACTFLOW_DEBUG=true` 时 `/docs` 渲染 Swagger UI）。

## 通用约定

- **Base URL**：`/api/v1/{group}`，group ∈ `auth / departments / chat / artifacts / stream / admin`
- **鉴权**：除 `/health/*` 与 `/auth/login` 外全部要求 `Authorization: Bearer <JWT>`；Admin 路由额外要求 `role=admin`
- **JSON**：请求与响应默认 `application/json`；文件上传用 `multipart/form-data`
- **时间**：所有 timestamp 用 ISO-8601 字符串
- **ID**：所有资源 ID 用字符串（非自增数字），避免跨存储后端的整型语义差异

### 状态码约定

| 码 | 含义 | 触发 |
|----|------|------|
| `200` | 成功 | 正常响应 |
| `400` | 请求/状态错误 | 旧密码错误 / 新密码与历史重用 / 全局 `ValueError`→400 兜底（如 bcrypt >72 字节、非 schema 路径的口令策略） |
| `401` | 未鉴权 | 缺 token / token 失效（含改密后 JWT 内嵌 `password_version` 与库不符）/ 用户被禁用 |
| `403` | 权限不足 | 非 admin 访问 `/admin/*` 端点；**或当前用户 `must_change_password=True`**（除查自身状态/改密外一律 403，见 [强制改密闸门](#强制改密闸门)） |
| `404` | 资源不存在 | **也覆盖"跨用户访问"** — 见 Design Decision |
| `409` | 冲突 | Lease 冲突 / interrupt 已解决 |
| `410` | 资源失效 | `active-stream` 指向的 stream 已过期 |
| `422` | 请求不合法 | 参数校验（含口令强度、用户名字符）、文件过大、格式不支持 |
| `429` | 请求过频 | 登录失败累计超阈，锁定窗口内（per-username / per-IP） |
| `503` | 服务不可用 | Health ready 降级 |

### 404-not-403 原则

跨用户访问他人资源（`GET /chat/{someone_elses_conv_id}` 等）一律返回 **404** 而非 403，不泄露资源存在性。实现上 Repository 查询即带 `user_id` 过滤，"没找到"与"不是你的"在同一代码路径。

---

## Auth

`/api/v1/auth` — 登录与自助资料管理（admin 用户管理见下方 [Admin](#admin) 小节）。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/login` | — | 用户名密码换 JWT |
| GET | `/me` | 用户 | 当前用户信息 |
| POST | `/me/password` | 用户 | 自助改密（校验 `current_password`） |
| PATCH | `/me` | 用户 | 自助改 `display_name`（清空传 `""`） |

> 无自助注册：账号由 admin 通过 `POST /api/v1/admin/users` 或 `bulk-import` 创建。

### POST `/login`

```http
POST /api/v1/auth/login
{"username": "alice", "password": "..."}

200 OK
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "expires_in": 604800,                  // = JWT_EXPIRY_DAYS(默认 7) × 86400
  "user": {
    "id": "...", "username": "alice", "display_name": "Alice", "role": "user",
    "must_change_password": false,       // true → 前端须弹强制改密框（见下）
    "department_path": ["技术部", "后端组"] // root → leaf 部门名链；未分配 → null
  }
}
```

- 失败统一返回 **401**（用户不存在 / 密码错 / 账号禁用），不区分原因；用户不存在时也对固定假 hash 跑一次 bcrypt，使两条分支耗时恒定，避免时序枚举用户名
- **登录频控**：每次失败对 `user:{username}` 与 `ip:{client_ip}` 两个 key 各 +1；任一在 `LOGIN_FAILURE_WINDOW_SEC`（默认 900s）窗口内累计达 `LOGIN_MAX_FAILURES`（默认 5）→ 锁定窗口内一律 **429**（带 `Retry-After`），连正确密码也拒。per-username 是主防线，per-IP 补抓"同 IP 喷多个用户名"。per-IP 的 `client_ip` 只读 nginx 覆写的 `X-Real-IP`（**刻意不读可伪造的 `X-Forwarded-For`**），dev 无 nginx 时回落 `request.client.host`
- **无 `/refresh` 端点**：token 过期后客户端需重新 login

### 强制改密闸门

用户的 `must_change_password` 在三种情况下被置 True：admin 创建/批量导入的新用户（首次登录）、admin 重置其密码、口令龄超过 `PASSWORD_EXPIRY_DAYS`（默认 180 天；登录时判定，`password_changed_at` 为 NULL 视为已过期；`0` = 不强制到期）。

标志为 True 时，`get_current_user` 对该用户的**所有请求返回 403 `Password change required`**，仅放行两个端点让其脱困：

| 仍放行 | 用途 |
|---|---|
| `GET /api/v1/auth/me` | 拉取自身状态（含 `must_change_password`） |
| `POST /api/v1/auth/me/password` | 完成改密（成功即清除标志） |

前端据 `must_change_password` 弹不可关闭的改密框；后端 403 是绕过前端时的兜底。

### 口令强度策略

所有"写入新口令"的入口（`POST /me/password`、admin `POST /users`、`PUT /users/{id}` 带 password、CSV 导入）共用 `validate_password_strength`，由 config 常量驱动（operator 可调，非 API 参数）：长度 ≥ `PASSWORD_MIN_LENGTH`（默认 8）、同时含字母+数字+符号、拒弱口令黑名单 / 键盘行走 / 单一重复 / 连续序列。

- schema 字段校验失败（Pydantic validator 抛 `ValueError`）→ **422**
- CSV 导入逐行捕获 → 该行进 `failed`（不抛 HTTP 错）
- **登录不做强度校验**：老用户旧口令可能不达标，登录只鉴别、不二次卡策略

### POST `/me/password` — 自助改密

```http
POST /api/v1/auth/me/password
{"current_password": "...", "new_password": "..."}

204 No Content
```

- 旧密码校验失败 → `400 Current password is incorrect`
- `new_password` 走口令强度策略（floor 8 + 复杂度，**非旧版 `min_length=4`**），不达标 → `422`
- **不重用查重**：新口令不得与"最近 `PASSWORD_HISTORY_COUNT` 个用过的口令（含当前）"相同（默认 `1` = 仅 ≠ 当前），命中 → `400 新密码不能与最近使用过的密码相同`
- 改密后 `password_version++`；旧 token 立即失效（`get_current_user` 查 `password_version` 字段对比 JWT 内嵌版本号）。自助改密同时清除 `must_change_password`

### PATCH `/me` — 自助改 display_name

```http
PATCH /api/v1/auth/me
{"display_name": "Alice Liu"}        // 传 "" 则清空，传 null 不改
```

返回最新 `UserInfo`。安全敏感字段（role / is_active / password）在此端点**不受理**，必须走 admin `PUT /admin/users/{id}` 或 `POST /me/password`。

---

## Departments

`/api/v1/departments` — 邻接表组织树（`Department(id, parent_id, name)`）。**全部 admin 权限**。每级在同 `parent_id` 下 `name` 唯一（含根级，跨方言由 STORED 生成列 + UNIQUE 兜底）；`parent_id` `ondelete=RESTRICT` 防级联误删。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `` | 列出某父下的子部门（`parent_id` 缺省 → 一级） |
| GET | `/tree` | 完整部门树（节点带 `user_count` 直属用户数） |
| GET | `/{dept_id}` | 单查（含 `user_count` / `child_count`，删前展示用） |
| POST | `` | 显式创建 |
| PATCH | `/{dept_id}` | 改名（搬家走 `/move`） |
| POST | `/{dept_id}/move` | 搬家（含环检测） |
| DELETE | `/{dept_id}` | 删除（必须为空） |
| POST | `/resolve` | 路径 → 末级 dept_id（缺失层级自动建） |

### GET `` / GET `/tree`

```http
GET /api/v1/departments?parent_id=dept-xxx        # 缺省 = 顶层
200 OK
{"departments": [{"id":"...","parent_id":"...","name":"...","user_count":3,"child_count":1, ...}]}

GET /api/v1/departments/tree
200 OK
{"nodes": [{"id":"...", "name":"...", "user_count":15, "children":[{...}, ...]}, ...]}
```

`/tree` 一次性返回（部门表数量级几十~几百，不分页）；`user_count` 是**直属**用户数，子树合计由前端按需算。

### POST `` — 创建

```http
POST /api/v1/departments
{"name": "技术部", "parent_id": null}     // null = 顶层

200 OK <DepartmentResponse>
```

- `400`：`parent_id` 不存在
- `409`：同父下已有同名（前置 SELECT + DB UNIQUE 双层防线，并发抢创建走 IntegrityError → 409）

### PATCH `/{dept_id}` — 改名

```http
PATCH /api/v1/departments/dept-x
{"name": "新名称"}
```

`409`：同父下已有同名。`name` 不变 → no-op 直接返回。

### POST `/{dept_id}/move` — 搬家

```http
POST /api/v1/departments/dept-x/move
{"new_parent_id": "dept-y"}                // null = 搬到根
```

- `400`：`new_parent_id` 不存在 / **环检测失败**（不能搬到自己/自己子孙下）
- `409`：新父下已有同名

### DELETE `/{dept_id}` — 删除空部门

```http
DELETE /api/v1/departments/dept-x
204 No Content
```

非空 → `409` + body：

```json
{"detail": {"message": "Department is not empty", "user_count": 5, "child_count": 2}}
```

提示先迁走（批量改部门走 `/admin/users/bulk-action` `set_department`）。

### POST `/resolve` — 路径解析（admin 显式调用）

```http
POST /api/v1/departments/resolve
{"path": ["技术部", "后端组", "Kibana"]}    // 顶层 → 末级，缺失自动 INSERT

200 OK
{"id": "dept-xyz"}                          // 末级 id；空 path / 全空字符串 → null
```

供前端 cascader "+ 新建当前级" 与批量导入按行解析时复用；**自动建 dept** 是合法 admin 行为，不走 `POST /` 的 409 路径。并发同路径插入由 `IntegrityError` 重试 SELECT 兜住（最多 1 次）。

### 用户搜索按部门子树扩展

`GET /api/v1/admin/users?q=...` 的 `q` 不只匹配 `username` / `display_name`，也匹配部门名 ILIKE，并**展开整个子树**（搜根部门名返回该条线下全部用户）。实现见 `src/utils/department_tree.expand_subtree`。

---

## Chat 对话

`/api/v1/chat` — 所有对话、执行、消息树、permission 接续都在这一组。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `` | 用户 | 发送消息、启动执行，返回 `stream_url` |
| GET | `` | 用户 | 列出自己的对话（`limit` 1-100, `offset`, `q`） |
| GET | `/{conv_id}` | 用户 | 对话详情含消息树 |
| DELETE | `/{conv_id}` | 用户 | 删除对话（fire-and-forget，引擎自检 fail-soft） |
| POST | `/bulk-delete` | 用户 | 批量删除自己的会话（best-effort） |
| GET | `/{conv_id}/active-stream` | 用户 | 查询当前是否有运行中的 stream（用于重连） |
| POST | `/{conv_id}/inject` | 用户 | 运行中注入一条 user 消息 |
| POST | `/{conv_id}/cancel` | 用户 | 请求取消运行 |
| POST | `/{conv_id}/resume` | 用户 | Permission 审批后恢复运行 |
| GET | `/{conv_id}/messages/{msg_id}/events` | 用户 | 单个 message 的事件链（可观测性） |

> Compaction 现为**引擎内同步执行**（单次 LLM 调用 `input+output` 超阈值时触发），不再有手动触发端点；详见 [engine.md → Compaction 机制](../architecture/engine.md#compaction-机制)。

### POST `` — 发送消息

**始终是 `multipart/form-data`**（即使无附件）：一个 `payload` 表单字段装 `ChatRequest` 的 JSON 字符串，加可选的 `files`。**没有名为 `message` 的字段**——文本在 JSON 里叫 `user_input`。

```http
POST /api/v1/chat        Content-Type: multipart/form-data

payload = '{"user_input":"...","conversation_id":null,"parent_message_id":null,"force_compact":false}'
          # ChatRequest JSON 字符串(Form 字段)。conversation_id=null → 新建对话;
          # parent_message_id 指定分支父节点;force_compact=true 本轮强制压缩一次
files    = <可选附件，可多个>   # 起 turn 前同步转成 artifact(source=user_upload)

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

### DELETE `/{conv_id}` — 单条删除

- 不预查 active execution，**fire-and-forget**
- 若引擎正在跑：被删的 conversation 行被 controller post-processing 的 `exists()` 检查兜住，引擎跳过持久化，不抛 FK
- `404`：不存在 / 不属于当前用户（404-not-403）

### POST `/bulk-delete` — 批量删除

```http
POST /api/v1/chat/bulk-delete
{"ids": ["conv-1", "conv-2", ...]}     // 1-200, 同请求内自动去重

200 OK
{
  "deleted": ["conv-1"],
  "failed": [{"id": "conv-2", "reason": "not_found"}]
}
```

- `failed.reason` 词汇：仅 `not_found`（cross-user 与不存在统一归此，遵循 404-not-403）
- 与单条 DELETE 同样 fire-and-forget
- **不存在 admin 全局批量删** —— admin 角色边界 = 用户管理而非数据管理；如需清理某用户的会话，删该用户即可（FK CASCADE 自动级联）

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

`/api/v1/artifacts` — 读取、版本管理、二进制原件下载。**只读（全是 GET）**：artifact 的产生（agent 创建 / 用户上传）都不经此路由。

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/{session_id}` | 用户 | 列出 session 下所有 artifacts |
| GET | `/{session_id}/{artifact_id}` | 用户 | 获取内容 + 版本列表 |
| GET | `/{session_id}/{artifact_id}/versions/{version}` | 用户 | 获取指定版本 |
| GET | `/{session_id}/{artifact_id}/raw` | 用户 | 二进制原件字节（图片 inline，其余 attachment 下载；纯文本 artifact 无 blob → `404`） |

artifact 分两类，由响应里的 `has_blob` 判别：**文本类**（md/py/csv 等）`content` 即正文、可编辑可版本化；**二进制类**（上传的图片 / docx / pdf）`content` 为空、源不可变单版，字节走 `/raw`，`content_type` 即原件真实 MIME。富格式不做服务端文本转换——读/转换是沙盒能力（见 [../architecture/artifacts.md](../architecture/artifacts.md)）。

### 上传走 `POST /chat`，不在本路由

旧的 `POST /artifacts/upload` 与 `POST /{session_id}/upload`（即时 commit）已删除。上传现在并入消息提交：

- `POST /api/v1/chat`，`multipart/form-data`：文本走 `payload` 表单字段（`ChatRequest` JSON，文本键为 `user_input`），附件走 `files`（可多文件），同一请求。**没有 `message` 字段**——按 `message + files` 调会因缺 `payload` 直接 422
- 大小上限：单文件 `config.MAX_UPLOAD_SIZE`（环境可配，默认 100MB，见 [deployment.md](../deployment.md)）；批量总字节由代理层独立封顶（200MB → `413`）。**注**：纯文本/未知扩展走转换兜底路径的文件另有更低的独立上限 `config.MAX_TEXT_CONVERT_BYTES`（默认 20MB）——文本整份变成 artifact `content`（无 blob），故比图片/PDF/docx 收得紧；超限同样 `422`。`422` 触发：超限（含文本闸）、格式不支持（`convert_uploaded_file` 在写库前做 size-check + 转换）
- 转换后的内容 closure-carry 进引擎，在 turn 起点经 `create_from_upload` **stage 进 WorkingSet**（发 `ARTIFACT_CREATED`、随 turn 末 `flush_all` 落库），与 agent 自建 artifact 走**同一统一生命周期**——不再绕过 write-back、不再即时 commit（见 [../architecture/artifacts.md](../architecture/artifacts.md)）

### 读取的即时性

删除 `_active_managers` overlay 后，所有 GET **均为纯 DB 读**（请求级 `ArtifactService` 自带空 WorkingSet）：

- turn **执行中**，REST 返回的是上一次 `flush_all` 的快照，**落后于 live**；turn 内的实时内容由 SSE 的 `ARTIFACT_CREATED` / `ARTIFACT_UPDATED` 事件推给前端 reduce（见 [streaming.md](../architecture/streaming.md) / [artifacts.md](../architecture/artifacts.md)），不经 REST
- `GET .../versions/{version}` 与 `GET .../raw` 同样只读 DB：执行中未 flush 的中间版本 / 本轮刚上传未落库的 blob 返回 `404`
- 前端据此在流式执行期间隐藏版本选择器 / 下载入口（纯前端读类 UX 锁，后端保持宽松）；本轮上传的图片由前端 send-local File 缓存即时渲染、二进制卡片只靠事件元数据渲染，均不在 turn 内打 `/raw`

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
data: {"type":"tool_complete","timestamp":"...","agent":"research_agent","data":{...}}
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
| POST | `/users` | 创建用户 |
| GET | `/users` | 列出用户（`limit` 1-200, `offset`, `q` 搜索按用户名/显示名/部门子树） |
| GET | `/users/{user_id}` | 单查 |
| PUT | `/users/{user_id}` | 更新 `display_name` / `password` / `role` / `is_active` / `department_id` |
| DELETE | `/users/{user_id}` | 硬删（FK CASCADE 连带删会话/消息/事件/工件） |
| GET | `/users/{user_id}/impact` | 删前 impact：`{conversation_count}` |
| POST | `/users/bulk-import` | CSV 批量导入用户（multipart） |
| POST | `/users/bulk-action` | 批量动作：disable/enable/delete/set_department |
| GET | `/users/bulk-impact` | 批量删前 impact：`{user_count, conversation_count}` |

> **路由注册顺序**：`/users/bulk-impact` 必须早于 `/users/{user_id}` 注册，否则会被解析为 `user_id="bulk-impact"`。bulk-action 同名地放在那之后纯粹是聚类，POST 没有路由冲突。

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

### POST `/users` — 创建用户

- 字段：`username` (2-64, regex `^[A-Za-z0-9._-]+$`), `password`（≤128，且过[口令强度策略](#口令强度策略)）, `display_name?`, `role?` ∈ `user`/`admin`, `department_id?`
- 新建用户 `must_change_password=True` —— 首次登录即被[强制改密闸门](#强制改密闸门)拦下
- `409` 用户名已存在；`422` 口令不达标 / 用户名字符非法（schema 校验）；`400` 非法 role / `department_id` 不存在

### PUT `/users/{user_id}` — 更新

所有字段可选（`UpdateUserRequest`）。**self-protection** 阻止三类自我修改：

| 字段 | self 改动行为 |
|---|---|
| `password` | `403`（必须走 `/me/password` 校验旧密码） |
| `role` | `403`（admin 不能 demote 自己） |
| `is_active` | `403`（admin 不能禁用自己） |
| `display_name` / `department_id` | 允许 |

`department_id` 用 Pydantic `model_fields_set` 区分"未传"与"显式 null（清空）"：

```http
PUT /api/v1/admin/users/u-abc
{"department_id": null}              // 清空归属
{"display_name": "..."}              // 仅改 display_name，不动 department_id
```

非自身 admin 可以 demote / disable —— 仅自身被 self-protection 守住。

> 重置他人密码（传 `password`，同样过[口令强度策略](#口令强度策略)）→ 该用户 `must_change_password=True` + `password_version++`：强制其下次登录改密，且旧 token 全端立即失效。

### DELETE `/users/{user_id}` — 硬删

- `403 Cannot delete yourself`
- `404 User not found`
- 成功 → `204`，**FK CASCADE** 连带删该用户的全部 conversations / messages / events / artifacts
- 若该用户当前有正在跑的 engine：被级联删的 conversation 行由 controller post-processing 的 `exists()` 检查兜住，engine 静默跳过持久化、不抛 FK 异常

### GET `/users/{user_id}/impact` — 删前 impact

```http
GET /api/v1/admin/users/u-abc/impact
200 OK
{"conversation_count": 17}
```

给前端 `DangerConfirmModal` 显示"将级联删除该用户的 N 条会话"。

### POST `/users/bulk-import` — CSV 批量导入

`multipart/form-data` 上传 CSV 文件。Header 必含 `username`，可选 `password` / `display_name` / `dept_l1` / `dept_l2` / `dept_l3`。

**关键语义：**

- **best-effort 三分类**：`created` / `failed` / `skipped`
- **密码每行必填**（`password` 列虽非 parse 阶段必填，但每行值不能空）：留空 → 该行进 `failed`（`reason="password is required (column must not be empty)"`）；提供则走[口令强度策略](#口令强度策略)，不达标 → `failed`（`reason="password does not meet policy: ..."`）。**不再有"留空 = 用户名"的默认行为**——admin 自填初始口令并带外分发
- **所有导入用户 `must_change_password=True`**：首次登录即被强制改密
- **部门路径**：`(dept_l1, dept_l2, dept_l3)` 走 `resolve_department_path` 自动建表；gap（中间空、后面非空）严格拒绝
- **文件内 username 重复 → 整体 400**（admin 必须先在源文件去重）
- **行数上限**：`MAX_BULK_IMPORT_ROWS=1000`，超 → 400
- **字节上限**：`MAX_BULK_IMPORT_BYTES=5MB`，超 → 422
- 编码：`charset-normalizer` 自动 sniff（UTF-8 / GBK 等），结果回到 `detected_encoding`
- bcrypt hash 阶段并行（`asyncio.gather + asyncio.to_thread`），300 行约 6 秒，event loop 不卡

```http
POST /api/v1/admin/users/bulk-import
Content-Type: multipart/form-data
file=@users.csv

200 OK
{
  "created": [<UserResponse>, ...],
  "failed": [{"row": 5, "username": "x y", "reason": "username has invalid characters"}],
  "skipped": [{"row": 9, "username": "alice", "reason": "username_exists"}],
  "total_rows": 12,
  "detected_encoding": "utf-8",
  "warnings": ["Unknown column 'note' ignored"]
}
```

### POST `/users/bulk-action` — 批量动作

```http
POST /api/v1/admin/users/bulk-action
{
  "ids": ["u-1", "u-2", ...],         // 1-200, 同请求内自动去重
  "action": "disable",                 // | enable | delete | set_department
  "payload": null                      // set_department 时 = {"department_id": "dept-x" | null}
}

200 OK
{
  "succeeded": ["u-1", "u-2"],
  "failed": [{"id": "u-3", "reason": "forbidden_self"}]
}
```

- `failed.reason` 词汇：`forbidden_self`（自己的 id，self-protection）/ `not_found` / `internal_error`（IntegrityError 等已 rollback 的并发场景）
- **set_department 预校验**：`payload.department_id` 在循环外查存在性，不存在 → 整批 400（fail-fast）
- **IntegrityError 处理**：单条 IntegrityError（如 `set_department` 期间 dept 被并发删除）→ rollback session + 该条进 failed + 后续行不受影响
- **其他异常**冒泡为 5xx（loud failure，CLAUDE.md "不为不会发生的场景加防御"）

### GET `/users/bulk-impact` — 批量删前 impact

```http
GET /api/v1/admin/users/bulk-impact?ids=u-1&ids=u-2&ids=u-3

200 OK
{"user_count": 3, "conversation_count": 27}
```

`user_count` = 请求 ids 去重后的数量（不区分是否存在）；`conversation_count` = 一次 IN 查询的会话总数。`ids` 上限 200。

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
- JWT 直接用较长 `expires_in`（默认 `JWT_EXPIRY_DAYS=7` → 604800s），过期重新 login — 省掉 refresh token 的存储与撤销复杂度。改密 / admin 重置通过 `password_version` 实现"软撤销"（旧 token 比对失败即 401），无需吊销集合
- 有强制失效需求可由 admin 通过 PUT `/users/{id}` 设 `is_active=false` 实现

### 为什么 Stream 用 `stream_id == message_id`

- 一次执行对应一个 message，ID 天然一对一，省去映射表
- 重连时客户端无需保存独立的 `stream_id`，只要记得上次的 `message_id`
- 权限校验天然走"这个 message 是不是你的"

### 为什么 Inject / Cancel / Resume 都走 POST `/chat/{id}/...`

- 都是对"运行中的执行"的副作用动作，资源定位都是 `conv_id`
- 副作用天然非幂等（inject 会排队、cancel 会设标志），POST 语义契合
- 统一的路径 prefix 让前端 api client 可以共享 conv 级别的 wrapper
