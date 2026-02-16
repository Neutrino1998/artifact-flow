# API Reference

ArtifactFlow API 文档，供前端集成使用。

> **注意：** 修改 `src/api/schemas/` 下的 Pydantic model 后，需要同步更新前端 TypeScript 类型。详见 [frontend/README.md — API 类型同步](../frontend/README.md#api-类型同步)。

## 基础信息

| 项目 | 值 |
|------|-----|
| Base URL | `http://localhost:8000/api/v1` |
| 协议 | HTTP/HTTPS |
| 数据格式 | JSON |
| 流式推送 | Server-Sent Events (SSE) |
| 认证方式 | Bearer Token (JWT) |

## 认证

所有 API 端点（除 `/health` 和 `/auth/login`）均需要 JWT 认证。

### 获取 Token

```
POST /auth/login
```

```json
{
  "username": "admin",
  "password": "your_password"
}
```

**响应：**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 604800,
  "user": {
    "id": "user-xxx",
    "username": "admin",
    "display_name": "admin",
    "role": "admin"
  }
}
```

### 使用 Token

所有后续请求需携带 `Authorization` header：

```
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
```

未携带或 token 过期返回 `401 Unauthorized`。访问其他用户的资源返回 `404 Not Found`（不暴露存在性）。

### 用户管理（仅 Admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/auth/me` | 当前用户信息 |
| POST | `/auth/users` | 创建用户 |
| GET | `/auth/users` | 用户列表 |
| PUT | `/auth/users/{id}` | 更新/禁用用户 |

---

## 快速开始

### 典型调用流程

> 此图为前端集成视角的简化版，完整的内部流程见 [Request Lifecycle — 整体流程](request-lifecycle.md#整体流程)。

```mermaid
sequenceDiagram
    participant Frontend
    participant API

    Frontend->>API: POST /chat {content}
    API->>Frontend: {conversation_id, thread_id, message_id, stream_url}

    Frontend->>API: GET /stream/{thread_id}
    loop SSE Events
        API->>Frontend: event: llm_chunk + data: {...}
        API->>Frontend: event: tool_complete + data: {...}
    end
    API->>Frontend: event: complete + data: {...}

    Frontend->>API: GET /chat/{conversation_id}
    API->>Frontend: 对话详情
```

---

## Chat API

### 发送消息

```
POST /chat
```

**请求体：**

```json
{
  "content": "帮我分析一下 Python 异步编程",
  "conversation_id": null,
  "parent_message_id": null
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `content` | string | 是 | 用户消息内容 |
| `conversation_id` | string | 否 | 对话 ID，null 则创建新对话 |
| `parent_message_id` | string \| null | 否 | 父消息 ID，用于分支对话。显式传 `null` 创建新的根消息；不传则自动追加到当前活跃分支 |

**响应：**

```json
{
  "conversation_id": "conv-550e8400e29b41d4a716446655440000",
  "message_id": "msg-770e8400e29b41d4a716446655440002",
  "thread_id": "thd-660e8400e29b41d4a716446655440001",
  "stream_url": "/api/v1/stream/thd-660e8400e29b41d4a716446655440001"
}
```

**说明：** 请求立即返回，实际执行在后台进行。使用 `stream_url` 连接 SSE 获取执行事件。

---

### 获取对话列表

```
GET /chat
```

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 20 | 返回数量 |
| `offset` | int | 0 | 偏移量 |

**响应：**

```json
{
  "conversations": [
    {
      "id": "conv-550e8400e29b41d4a716446655440000",
      "title": "Python 异步编程分析",
      "message_count": 5,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T11:00:00Z"
    }
  ],
  "total": 42,
  "has_more": true
}
```

---

### 获取对话详情

```
GET /chat/{conversation_id}
```

**响应：**

```json
{
  "id": "conv-550e8400e29b41d4a716446655440000",
  "title": "Python 异步编程分析",
  "active_branch": "msg-002",
  "messages": [
    {
      "id": "msg-001",
      "parent_id": null,
      "content": "帮我分析一下 Python 异步编程",
      "response": "好的，让我来分析...",
      "created_at": "2024-01-15T10:30:00Z",
      "children": ["msg-002"]
    },
    {
      "id": "msg-002",
      "parent_id": "msg-001",
      "content": "能详细说说 asyncio 吗？",
      "response": "asyncio 是...",
      "created_at": "2024-01-15T10:35:00Z",
      "children": []
    }
  ],
  "session_id": "conv-550e8400e29b41d4a716446655440000",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:35:00Z"
}
```

**说明：** `messages` 是扁平数组，通过 `parent_id` 和 `children` 构建树状结构。`session_id` 与 `conversation_id` 相同。

---

### 删除对话

```
DELETE /chat/{conversation_id}
```

**响应：**

```json
{
  "success": true,
  "message": "Conversation 'conv-xxx' deleted"
}
```

---

### 恢复权限中断

当工具需要用户确认时使用：

```
POST /chat/{conversation_id}/resume
```

**请求体：**

```json
{
  "thread_id": "thd-660e8400e29b41d4a716446655440001",
  "message_id": "msg-770e8400e29b41d4a716446655440002",
  "approved": true
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `thread_id` | string | 是 | 中断时的 thread_id |
| `message_id` | string | 是 | 要更新的消息 ID |
| `approved` | boolean | 是 | 是否批准执行 |

**响应：**

```json
{
  "stream_url": "/api/v1/stream/thd-660e8400e29b41d4a716446655440001"
}
```

**说明：** 返回新的 SSE 端点 URL，前端需重新连接以接收后续事件。

---

## Stream API

### SSE 事件流

```
GET /stream/{thread_id}
```

**连接方式：**

SSE 端点需要认证，必须使用 `fetch`（而非 `EventSource`）以携带 `Authorization` header：

```javascript
const token = localStorage.getItem('af_token');
const res = await fetch('/api/v1/stream/{thread_id}', {
  headers: {
    Accept: 'text/event-stream',
    Authorization: `Bearer ${token}`,
  },
});
// 用 ReadableStream 解析 SSE 事件流
```

**事件格式说明：**

使用标准 SSE `event:` 字段区分事件类型，`data:` JSON 内同时保留 `type` 字段：

```
event: event_type
data: {"type":"event_type","timestamp":"...","agent":"...","data":{...}}
```

前端使用 `addEventListener` 按事件类型独立监听。

**事件类型：**

#### metadata

执行开始，返回元数据。

```json
{
  "type": "metadata",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "data": {
    "conversation_id": "conv-xxx",
    "thread_id": "thd-yyy",
    "message_id": "msg-zzz"
  }
}
```

#### agent_start

Agent 开始执行。

```json
{
  "type": "agent_start",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "lead_agent"
}
```

#### llm_chunk

LLM 流式输出片段。

**注意：** `content` 是**累积**的内容（从本轮 LLM 调用开始到当前的所有输出），而非增量 delta。前端如需增量显示，应自行计算差值。

```json
{
  "type": "llm_chunk",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "lead_agent",
  "data": {
    "success": true,
    "content": "让我来分析",
    "reasoning_content": null,
    "metadata": {
      "agent": "lead_agent",
      "model": "claude-sonnet-4-20250514",
      "started_at": "2024-01-15T10:30:00.000Z"
    },
    "routing": null,
    "token_usage": null
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 累积的 LLM 输出内容 |
| `reasoning_content` | string\|null | 推理内容（如使用 extended thinking） |
| `metadata` | object | Agent 元数据 |
| `routing` | object\|null | 路由信息（仅在解析出工具调用时有值） |
| `token_usage` | object\|null | Token 统计（仅在 LLM 完成后有值） |

#### llm_complete

LLM 单次调用完成（流式结束）。

```json
{
  "type": "llm_complete",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "lead_agent",
  "data": {
    "success": true,
    "content": "完整的输出内容...",
    "reasoning_content": null,
    "metadata": {
      "agent": "lead_agent",
      "model": "claude-sonnet-4-20250514",
      "started_at": "2024-01-15T10:30:00.000Z"
    },
    "routing": null,
    "token_usage": {
      "input_tokens": 1000,
      "output_tokens": 500
    }
  }
}
```

**说明：** 此时 `token_usage` 已填充，后续 `agent_complete` 事件会携带完整的路由信息。

#### agent_complete

Agent 单轮执行完成（可能携带工具调用路由）。

```json
{
  "type": "agent_complete",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "lead_agent",
  "data": {
    "success": true,
    "content": "Agent 的完整响应内容",
    "reasoning_content": null,
    "metadata": {
      "agent": "lead_agent",
      "model": "claude-sonnet-4-20250514",
      "started_at": "2024-01-15T10:30:00.000Z"
    },
    "routing": {
      "type": "tool_call",
      "tool_name": "web_search",
      "params": {"query": "..."}
    },
    "token_usage": {
      "input_tokens": 1000,
      "output_tokens": 500
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | Agent 的完整输出内容 |
| `routing` | object\|null | 路由信息：`null` 表示完成，否则包含工具调用或子 agent 调用 |
| `routing.type` | string | `"tool_call"` 或 `"subagent"` |
| `routing.tool_name` | string | 工具名称（tool_call 时） |
| `routing.target` | string | 子 agent 名称（subagent 时） |

#### tool_start

工具开始执行。

```json
{
  "type": "tool_start",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "search_agent",
  "tool": "web_search",
  "data": {
    "params": {
      "query": "Python async programming"
    }
  }
}
```

#### tool_complete

工具执行完成。包含工具调用参数和返回数据，前端可用于追踪工具执行详情（如 Artifact 操作的 `params.id`）。

```json
{
  "type": "tool_complete",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "search_agent",
  "tool": "web_search",
  "data": {
    "success": true,
    "duration_ms": 1234,
    "error": null,
    "params": {
      "query": "Python async programming"
    },
    "result_data": "<search_results>...</search_results>"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 工具是否执行成功 |
| `duration_ms` | int | 工具执行耗时（毫秒） |
| `error` | string\|null | 错误信息（失败时） |
| `params` | object | 工具调用参数（与 `tool_start` 中的 `params` 相同） |
| `result_data` | any\|null | 工具返回的业务数据（成功时），失败时为 `null` |

**`result_data` 示例（按工具类型）：**

| 工具 | result_data |
|------|-------------|
| `web_search` | XML 字符串（搜索结果） |
| `web_fetch` | XML 字符串（抓取内容） |
| `create_artifact` | `{"message": "Created artifact 'plan'"}` |
| `update_artifact` | `{"message": "...", "version": 2}` |
| `rewrite_artifact` | `{"message": "...", "version": 3}` |

#### permission_request

请求用户确认权限。

```json
{
  "type": "permission_request",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "crawl_agent",
  "tool": "read_file",
  "data": {
    "permission_level": "confirm",
    "params": {
      "path": "/etc/config"
    }
  }
}
```

**处理方式：** 显示确认对话框，用户确认后调用 `POST /chat/{id}/resume`。

#### permission_result

权限确认结果。

```json
{
  "type": "permission_result",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "agent": "crawl_agent",
  "tool": "read_file",
  "data": {
    "approved": true
  }
}
```

#### complete

执行完成（正常完成或权限中断）。

```json
{
  "type": "complete",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "data": {
    "success": true,
    "interrupted": false,
    "conversation_id": "conv-xxx",
    "message_id": "msg-yyy",
    "thread_id": "thd-zzz",
    "response": "最终响应内容",
    "execution_metrics": {
      "started_at": "2024-01-15T10:30:00.000Z",
      "completed_at": "2024-01-15T10:30:05.000Z",
      "total_duration_ms": 5000,
      "agent_executions": [...],
      "tool_calls": [...]
    }
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 执行是否成功 |
| `interrupted` | boolean | 是否因权限中断（需要用户确认） |
| `conversation_id` | string | 对话 ID |
| `message_id` | string | 消息 ID |
| `thread_id` | string | LangGraph 线程 ID |
| `response` | string | 最终响应内容（`interrupted=false` 时） |
| `interrupt_type` | string | 中断类型（`interrupted=true` 时） |
| `interrupt_data` | object | 中断数据（`interrupted=true` 时） |
| `execution_metrics` | object | 执行指标 |

**权限中断时的响应示例：**

```json
{
  "type": "complete",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "data": {
    "success": true,
    "interrupted": true,
    "conversation_id": "conv-xxx",
    "message_id": "msg-yyy",
    "thread_id": "thd-zzz",
    "interrupt_type": "tool_permission",
    "interrupt_data": {
      "type": "tool_permission",
      "tool_name": "read_file",
      "params": {"path": "/etc/config"},
      "permission_level": "confirm",
      "message": "Tool 'read_file' requires confirm permission"
    },
    "execution_metrics": {...}
  }
}
```

**处理方式：** 当 `interrupted=true` 时，前端应显示确认对话框，用户确认后调用 `POST /chat/{id}/resume`。

#### error

执行错误。

```json
{
  "type": "error",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "data": {
    "success": false,
    "conversation_id": "conv-xxx",
    "message_id": "msg-yyy",
    "thread_id": "thd-zzz",
    "error": "错误信息"
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | boolean | 始终为 `false` |
| `conversation_id` | string | 对话 ID |
| `message_id` | string | 消息 ID |
| `thread_id` | string | LangGraph 线程 ID |
| `error` | string | 错误信息 |

---

## Artifact API

**说明：** Artifact 使用 `(session_id, artifact_id)` 复合键标识。`session_id` 与 `conversation_id` 相同。

### 获取 Artifact 列表

```
GET /artifacts/{session_id}
```

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID（与 conversation_id 相同） |

**响应：**

```json
{
  "session_id": "conv-550e8400e29b41d4a716446655440000",
  "artifacts": [
    {
      "id": "task_plan",
      "content_type": "markdown",
      "title": "任务计划",
      "current_version": 3,
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T11:00:00Z"
    }
  ]
}
```

---

### 获取 Artifact 详情

```
GET /artifacts/{session_id}/{artifact_id}
```

**响应：**

```json
{
  "id": "task_plan",
  "session_id": "conv-550e8400e29b41d4a716446655440000",
  "content_type": "markdown",
  "title": "任务计划",
  "content": "# 任务计划\n\n## 步骤 1\n...",
  "current_version": 3,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T11:00:00Z"
}
```

---

### 获取版本历史

```
GET /artifacts/{session_id}/{artifact_id}/versions
```

**响应：**

```json
{
  "artifact_id": "task_plan",
  "session_id": "conv-550e8400e29b41d4a716446655440000",
  "versions": [
    {
      "version": 3,
      "update_type": "update",
      "created_at": "2024-01-15T11:00:00Z"
    },
    {
      "version": 2,
      "update_type": "update",
      "created_at": "2024-01-15T10:45:00Z"
    },
    {
      "version": 1,
      "update_type": "create",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

---

### 获取特定版本

```
GET /artifacts/{session_id}/{artifact_id}/versions/{version}
```

**响应：**

```json
{
  "version": 2,
  "content": "第二版内容...",
  "update_type": "update",
  "changes": [["旧内容", "新内容"]],
  "created_at": "2024-01-15T10:45:00Z"
}
```

---

## 错误处理

### 错误响应格式

使用 FastAPI 默认格式：

```json
{
  "detail": "Invalid username or password"
}
```

验证错误（422）使用 Pydantic 格式：

```json
{
  "detail": [
    {
      "loc": ["body", "content"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

### 常见错误状态码

| HTTP 状态 | 场景 |
|----------|------|
| 401 | 未认证或 token 过期 |
| 403 | 权限不足（如非 Admin 访问管理端点） |
| 404 | 资源不存在（或无权访问，跨用户访问统一返回 404） |
| 409 | 冲突（如用户名已存在、Artifact 版本冲突） |
| 422 | 请求参数验证失败 |
| 500 | 服务器内部错误 |

---

## 前端集成示例

### 完整的聊天组件

```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: Date;
}

interface PermissionRequest {
  threadId: string;
  messageId: string;
  tool: string;
  params: Record<string, any>;
  permissionLevel: string;
}

interface ChatState {
  conversationId: string | null;
  messages: Message[];
  isStreaming: boolean;
  currentContent: string;
  pendingPermission: PermissionRequest | null;
  currentThreadId: string | null;
  currentMessageId: string | null;
}

async function sendMessage(
  state: ChatState,
  content: string
): Promise<void> {
  const token = localStorage.getItem('af_token');
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};

  // 1. 发送消息
  const res = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: JSON.stringify({
      content,
      conversation_id: state.conversationId
    })
  });

  const { conversation_id, thread_id, message_id, stream_url } = await res.json();
  state.conversationId = conversation_id;
  state.currentThreadId = thread_id;
  state.currentMessageId = message_id;
  state.isStreaming = true;
  state.currentContent = '';

  // 添加用户消息
  state.messages.push({
    id: message_id,
    role: 'user',
    content,
    createdAt: new Date()
  });

  // 2. 连接 SSE（使用 fetch 以支持自定义 Authorization header）
  const sseRes = await fetch(stream_url, {
    headers: { Accept: 'text/event-stream', ...authHeaders },
  });

  if (sseRes.status === 401) {
    // Token 过期或无效，需重新登录
    console.error('Session expired');
    return;
  }

  const reader = sseRes.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // 3. 逐块读取 SSE 事件
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    let eventType = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        eventType = line.slice(7);
      } else if (line.startsWith('data: ') && eventType) {
        const parsed = JSON.parse(line.slice(6));
        handleSSEEvent(state, eventType, parsed, thread_id, message_id);
        eventType = '';
      }
    }
  }
}

function handleSSEEvent(
  state: ChatState,
  eventType: string,
  parsed: any,
  threadId: string,
  messageId: string
): void {
  const { data } = parsed;

  switch (eventType) {
    case 'llm_chunk':
      // data.content 是累积内容，直接使用
      state.currentContent = data.content;
      break;

    case 'agent_complete':
      if (data.routing) state.currentContent = '';
      break;

    case 'permission_request':
      state.pendingPermission = {
        threadId, messageId,
        tool: parsed.tool,
        params: data.params,
        permissionLevel: data.permission_level,
      };
      break;

    case 'complete':
      if (data.interrupted) {
        console.log('Interrupted:', data.interrupt_data);
      } else {
        state.messages.push({
          id: `${messageId}_response`,
          role: 'assistant',
          content: data.response,
          createdAt: new Date(),
        });
      }
      state.currentContent = '';
      state.isStreaming = false;
      break;

    case 'error':
      console.error('Execution error:', data.error);
      state.isStreaming = false;
      break;
  }
}

async function handlePermission(
  state: ChatState,
  approved: boolean
): Promise<void> {
  if (!state.pendingPermission) return;

  const token = localStorage.getItem('af_token');
  const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};

  const res = await fetch(`/api/v1/chat/${state.conversationId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders },
    body: JSON.stringify({
      thread_id: state.pendingPermission.threadId,
      message_id: state.pendingPermission.messageId,
      approved
    })
  });

  const { stream_url } = await res.json();
  state.pendingPermission = null;
  state.isStreaming = true;

  // 重新连接 SSE（同样使用 fetch + auth header）
  const sseRes = await fetch(stream_url, {
    headers: { Accept: 'text/event-stream', ...authHeaders },
  });
  // ... 用 ReadableStream 处理后续事件（同 sendMessage）
}
```

### Artifact 查看组件

```typescript
function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('af_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function loadArtifacts(sessionId: string): Promise<Artifact[]> {
  const res = await fetch(`/api/v1/artifacts/${sessionId}`, {
    headers: getAuthHeaders(),
  });
  const { artifacts } = await res.json();
  return artifacts;
}

async function loadArtifactWithHistory(
  sessionId: string,
  artifactId: string
): Promise<{ artifact: Artifact; versions: Version[] }> {
  const headers = getAuthHeaders();
  const [artifactRes, versionsRes] = await Promise.all([
    fetch(`/api/v1/artifacts/${sessionId}/${artifactId}`, { headers }),
    fetch(`/api/v1/artifacts/${sessionId}/${artifactId}/versions`, { headers })
  ]);

  return {
    artifact: await artifactRes.json(),
    versions: (await versionsRes.json()).versions
  };
}

async function loadSpecificVersion(
  sessionId: string,
  artifactId: string,
  version: number
): Promise<VersionDetail> {
  const res = await fetch(
    `/api/v1/artifacts/${sessionId}/${artifactId}/versions/${version}`,
    { headers: getAuthHeaders() }
  );
  return await res.json();
}
```
