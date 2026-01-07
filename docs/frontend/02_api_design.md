# ArtifactFlow API 层实现方案

> 版本: v1.1 | 依赖: 持久化改造完成
> 
> **v1.1 更新**：
> - 新增 Section 2.1：全链路异步 I/O 开发标准
> - 新增 Section 6.4：事件缓冲队列设计（含 TTL 机制）
> - 更新 Section 7.2：明确 SSE 连接生命周期管理

## 1. 设计目标

为前端提供完整的 API 接口，支持：

1. **流式输出**：实时推送 Agent 执行过程（LLM 输出、工具调用、权限请求）
2. **CRUD 操作**：对话管理、Artifact 管理
3. **状态同步**：Artifact 版本获取、对话树查询
4. **扩展预留**：用户认证接口预留

---

## 2. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| Web 框架 | FastAPI | 原生异步、自动 OpenAPI 文档、类型安全 |
| 流式传输 | SSE (Server-Sent Events) | 单向推送足够、比 WebSocket 简单、自动重连 |
| 序列化 | Pydantic v2 | 与 FastAPI 深度集成、性能优秀 |
| CORS | fastapi.middleware.cors | 支持前端跨域访问 |

**为什么选 SSE 而不是 WebSocket**：
- 当前需求是单向推送（Server → Client）
- SSE 更轻量，自带重连机制
- 浏览器原生支持 EventSource API
- 如果后续需要双向通信（如协作编辑），再引入 WebSocket

### 2.1 全链路异步 I/O 开发标准 🆕

> **核心原则**：防止单个耗时操作（如 LLM 推理、网页爬取）阻塞 Worker 进程，导致高并发下系统假死。

**强制要求**：

| 操作类型 | ✅ 必须使用 | ❌ 禁止使用 |
|---------|------------|-----------|
| HTTP 请求 | `httpx.AsyncClient` | `requests` |
| 数据库 | `aiosqlite` / `asyncpg` | `sqlite3` / `psycopg2` |
| 文件操作 | `aiofiles` | 内置 `open()` (大文件) |
| 进程/线程 | `asyncio.to_thread()` | `threading.Thread` 直接调用 |

**代码示例**：

```python
# ✅ 正确：异步 HTTP 请求
async def fetch_external_api():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.example.com/data")
        return response.json()

# ❌ 错误：同步阻塞
def fetch_external_api_wrong():
    response = requests.get("https://api.example.com/data")  # 会阻塞整个 Worker
    return response.json()

# ✅ 正确：CPU 密集型任务包装
async def cpu_intensive_task(data):
    result = await asyncio.to_thread(heavy_computation, data)
    return result
```

**Lint 检查建议**：
- 在 CI 中添加 `flake8-async` 或自定义规则，检测同步阻塞调用

---

## 3. API 架构设计

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│                   (Next.js Application)                      │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
        ┌─────────┐    ┌───────────┐    ┌─────────┐
        │   SSE   │    │  REST API │    │  (预留)  │
        │ /stream │    │  /api/v1  │    │  Auth   │
        └─────────┘    └───────────┘    └─────────┘
              │               │               │
              └───────────────┼───────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │   Routers   │  │   Schemas   │  │    Dependencies     │ │
│  │ - chat      │  │ - request   │  │ - get_controller    │ │
│  │ - artifact  │  │ - response  │  │ - get_artifact_mgr  │ │
│  │ - stream    │  │ - event     │  │ - (get_current_user)│ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              StreamManager (新增)                    │   │
│  │  - 事件缓冲队列 (asyncio.Queue)                      │   │
│  │  - TTL 管理 (防止内存泄漏)                           │   │
│  │  - 连接状态追踪                                      │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Core Layer (现有)                       │
│         ExecutionController, ArtifactManager, etc.          │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 URL 结构设计

```
/api/v1/
├── chat/                           # 对话相关
│   ├── POST   /                    # 发送消息（返回 stream URL）
│   ├── GET    /                    # 列出所有对话
│   ├── GET    /{conv_id}           # 获取对话详情（含消息树）
│   ├── DELETE /{conv_id}           # 删除对话
│   └── POST   /{conv_id}/resume    # 恢复中断的执行
│
├── artifacts/                      # Artifact 相关
│   ├── GET    /{session_id}        # 列出 session 下所有 artifacts
│   ├── GET    /{session_id}/{artifact_id}           # 获取 artifact 详情
│   ├── GET    /{session_id}/{artifact_id}/versions  # 获取版本列表
│   ├── GET    /{session_id}/{artifact_id}/versions/{version}  # 获取特定版本
│   └── GET    /{session_id}/{artifact_id}/diff      # 获取版本间差异（可选）
│
└── stream/                         # 流式输出
    └── GET    /{thread_id}         # SSE 端点，订阅执行过程
```

---

## 4. 接口详细设计

### 4.1 对话接口

#### POST /api/v1/chat
发送新消息，启动 Graph 执行。

**Request Body**:
```json
{
  "content": "string",                    // 用户消息
  "conversation_id": "string | null",     // 可选：继续现有对话
  "parent_message_id": "string | null"    // 可选：分支对话的父消息
}
```

**Response**:
```json
{
  "conversation_id": "conv-xxx",
  "message_id": "msg-xxx",
  "thread_id": "thd-xxx",
  "stream_url": "/api/v1/stream/thd-xxx"  // 前端订阅此 URL 获取流式输出
}
```

**设计要点**：
- 不直接返回结果，而是返回 `stream_url`
- 前端通过 SSE 订阅获取实时更新
- 支持乐观 UI（先显示用户消息，再等待响应）

#### POST /api/v1/chat/{conv_id}/resume
恢复中断的执行（权限确认后）。

**Request Body**:
```json
{
  "thread_id": "thd-xxx",
  "approved": true
}
```

**Response**:
```json
{
  "stream_url": "/api/v1/stream/thd-xxx"  // 新的 stream URL
}
```

**🆕 重要说明**：
- 每次 resume 返回的 `stream_url` 可能相同，但前端应**销毁旧的 EventSource 实例**后再建立新连接
- 这确保了连接状态的干净切换

#### GET /api/v1/chat
列出对话列表。

**Query Parameters**:
- `limit`: 数量限制（默认 20）
- `offset`: 偏移量（分页）
- `user_id`: 预留，用户 ID 过滤

**Response**:
```json
{
  "conversations": [
    {
      "id": "conv-xxx",
      "title": "关于量子计算的讨论",
      "message_count": 5,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T01:00:00Z"
    }
  ],
  "total": 100,
  "has_more": true
}
```

#### GET /api/v1/chat/{conv_id}
获取对话详情，包含完整的消息树。

**Response**:
```json
{
  "id": "conv-xxx",
  "title": "关于量子计算的讨论",
  "active_branch": "msg-latest",
  "messages": [
    {
      "id": "msg-1",
      "parent_id": null,
      "content": "什么是量子计算？",
      "response": "量子计算是...",
      "created_at": "...",
      "children": ["msg-2", "msg-3"]  // 子消息 ID 列表（用于渲染树）
    }
  ],
  "session_id": "sess-conv-xxx",  // 关联的 artifact session
  "created_at": "...",
  "updated_at": "..."
}
```

**设计要点**：
- `messages` 是扁平数组，通过 `parent_id` 和 `children` 表达树结构
- 前端可以选择只渲染当前分支路径，或展示完整树

### 4.2 Artifact 接口

#### GET /api/v1/artifacts/{session_id}
列出 session 下所有 artifacts。

**Response**:
```json
{
  "session_id": "sess-xxx",
  "artifacts": [
    {
      "id": "research_report",
      "content_type": "markdown",
      "title": "AI Research Report",
      "current_version": 3,
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

#### GET /api/v1/artifacts/{session_id}/{artifact_id}
获取 artifact 详情（包含当前版本内容）。

**Response**:
```json
{
  "id": "research_report",
  "session_id": "sess-xxx",
  "content_type": "markdown",
  "title": "AI Research Report",
  "content": "# Report\n\n...",  // 当前版本内容
  "current_version": 3,
  "created_at": "...",
  "updated_at": "..."
}
```

#### GET /api/v1/artifacts/{session_id}/{artifact_id}/versions
获取版本历史列表。

**Response**:
```json
{
  "versions": [
    {
      "version": 1,
      "update_type": "create",
      "created_at": "..."
    },
    {
      "version": 2,
      "update_type": "update",
      "created_at": "..."
    }
  ]
}
```

#### GET /api/v1/artifacts/{session_id}/{artifact_id}/versions/{version}
获取特定版本的完整内容。

**Response**:
```json
{
  "version": 2,
  "content": "...",
  "update_type": "update",
  "changes": [["old text", "new text"]],  // 如果有记录
  "created_at": "..."
}
```

### 4.3 流式接口（SSE）

#### GET /api/v1/stream/{thread_id}
SSE 端点，推送 Graph 执行过程。

**Event Types**:

```
event: metadata
data: {"conversation_id": "...", "message_id": "...", "thread_id": "..."}

event: stream
data: {"type": "start", "agent": "lead_agent", "timestamp": "..."}

event: stream
data: {"type": "llm_chunk", "agent": "lead_agent", "data": {"content": "...", "reasoning_content": "..."}}

event: stream
data: {"type": "llm_complete", "agent": "lead_agent", "data": {"token_usage": {...}}}

event: stream
data: {"type": "tool_start", "agent": "lead_agent", "data": {"tool_name": "web_search"}}

event: stream
data: {"type": "tool_result", "agent": "lead_agent", "data": {"success": true}}

event: stream
data: {"type": "permission_required", "agent": "crawl_agent", "data": {"routing": {...}}}

event: complete
data: {"success": true, "interrupted": false, "response": "...", "artifacts_updated": ["research_report"]}

event: interrupt
data: {"success": true, "interrupted": true, "interrupt_type": "tool_permission", "interrupt_data": {...}}

event: error
data: {"error": "Something went wrong"}
```

**🆕 SSE 连接生命周期**：

| 事件 | 连接状态 | 说明 |
|------|---------|------|
| `metadata` | 保持 | 首个事件，确认连接成功 |
| `stream` | 保持 | 流式内容推送 |
| `complete` | **关闭** | 正常完成，服务端主动关闭连接 |
| `interrupt` | **关闭** | 需要用户操作，服务端主动关闭连接 |
| `error` | **关闭** | 发生错误，服务端主动关闭连接 |

**设计要点**：
- 使用标准 SSE 格式（`event:` + `data:`）
- 事件类型与 `ControllerEventType` 对应
- `stream` 事件的 `data` 直接转发 Graph 的 custom stream 内容
- `complete` / `interrupt` / `error` 事件后，**服务端主动关闭 SSE 连接**

---

## 5. 文件结构规划

### 5.1 新增文件

```
src/
├── api/                             # API 层
│   ├── __init__.py
│   ├── main.py                      # FastAPI 应用入口
│   ├── config.py                    # API 配置
│   ├── dependencies.py              # 依赖注入（Controller、Manager 等）
│   │
│   ├── routers/                     # 路由模块
│   │   ├── __init__.py
│   │   ├── chat.py                  # /api/v1/chat
│   │   ├── artifacts.py             # /api/v1/artifacts
│   │   └── stream.py                # /api/v1/stream (SSE)
│   │
│   ├── schemas/                     # Pydantic 模型
│   │   ├── __init__.py
│   │   ├── chat.py                  # 对话相关 schema
│   │   ├── artifact.py              # Artifact 相关 schema
│   │   └── events.py                # SSE 事件 schema
│   │
│   ├── services/                    # 🆕 服务层
│   │   ├── __init__.py
│   │   └── stream_manager.py        # 事件缓冲队列管理
│   │
│   └── utils/                       # API 工具函数
│       ├── __init__.py
│       └── sse.py                   # SSE 响应构建器
│
└── run_server.py                    # 服务器启动脚本
```

### 5.2 改造文件

| 文件 | 改造内容 |
|------|----------|
| `controller.py` | 添加获取执行状态的方法（用于 SSE 重连） |
| `requirements.txt` | 添加 `fastapi`, `uvicorn`, `sse-starlette`, `aiofiles` |

---

## 6. 核心组件设计

### 6.1 FastAPI 应用 (main.py)

**职责**：
- 创建 FastAPI 应用实例
- 配置 CORS 中间件
- 注册路由
- 配置异常处理

**设计要点**：
- 启动时初始化数据库连接
- 使用 lifespan context manager 管理资源
- 配置 CORS 允许前端域名

### 6.2 依赖注入 (dependencies.py)

**职责**：
- 提供 `get_controller()` 依赖
- 提供 `get_artifact_manager()` 依赖
- 提供 `get_stream_manager()` 依赖 🆕
- 预留 `get_current_user()` 依赖

**设计要点**：
- 使用 FastAPI 的 `Depends` 系统
- Controller 和 Manager 通过构造函数注入（参见持久化设计 v1.1）
- 用户依赖预留为返回 `None`（无认证时）

**示例代码**：
```python
# dependencies.py
from functools import lru_cache
from fastapi import Depends
from core.controller import ExecutionController
from api.services.stream_manager import StreamManager

@lru_cache()
def get_stream_manager() -> StreamManager:
    return StreamManager(ttl_seconds=30)

async def get_controller(
    stream_manager: StreamManager = Depends(get_stream_manager)
) -> ExecutionController:
    # 通过构造函数注入，而非全局单例
    return ExecutionController(
        compiled_graph=get_compiled_graph(),
        stream_manager=stream_manager
    )
```

### 6.3 SSE 路由 (stream.py)

**职责**：
- 订阅 Graph 执行过程
- 转发 `stream_execute` 的事件
- 处理连接断开

**设计要点**：
```
流程：
1. 前端 POST /chat 获取 thread_id
2. POST 处理器启动任务，事件写入 StreamManager 队列
3. 前端 EventSource 连接 /stream/{thread_id}
4. GET 处理器从队列消费事件，通过 SSE 推送
5. 收到 complete/interrupt/error 事件后关闭连接
```

### 6.4 事件缓冲队列设计（StreamManager）🆕

> **解决的问题**：POST /chat 启动任务后，Graph 可能在前端 SSE 连接建立之前就已经开始产生事件，导致 `metadata` / `start` 等早期事件丢失。

**架构设计**：

```
┌─────────────────────────────────────────────────────────────┐
│                      StreamManager                           │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  streams: Dict[thread_id, StreamContext]               │ │
│  │                                                        │ │
│  │  StreamContext:                                        │ │
│  │    - queue: asyncio.Queue[SSEEvent]                   │ │
│  │    - created_at: datetime                             │ │
│  │    - status: pending | streaming | closed             │ │
│  │    - ttl_task: asyncio.Task (自动清理)                 │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  方法:                                                       │
│    - create_stream(thread_id) → StreamContext               │
│    - push_event(thread_id, event)                          │
│    - consume_events(thread_id) → AsyncGenerator[SSEEvent]  │
│    - close_stream(thread_id)                               │
│    - cleanup_expired()  # 定期清理过期队列                   │
└─────────────────────────────────────────────────────────────┘
```

**TTL 机制**：

```python
# stream_manager.py

class StreamContext:
    queue: asyncio.Queue
    created_at: datetime
    status: Literal["pending", "streaming", "closed"]
    ttl_task: Optional[asyncio.Task]

class StreamManager:
    def __init__(self, ttl_seconds: int = 30):
        self.streams: Dict[str, StreamContext] = {}
        self.ttl_seconds = ttl_seconds
    
    def create_stream(self, thread_id: str) -> StreamContext:
        """创建事件队列，并启动 TTL 定时器"""
        context = StreamContext(
            queue=asyncio.Queue(),
            created_at=datetime.now(),
            status="pending",
            ttl_task=None
        )
        self.streams[thread_id] = context
        
        # 启动 TTL 定时器
        context.ttl_task = asyncio.create_task(
            self._ttl_cleanup(thread_id)
        )
        return context
    
    async def _ttl_cleanup(self, thread_id: str):
        """TTL 到期后自动清理队列（防止内存泄漏）"""
        await asyncio.sleep(self.ttl_seconds)
        
        context = self.streams.get(thread_id)
        if context and context.status == "pending":
            # 前端未连接，清理队列
            logger.warning(f"Stream {thread_id} expired (TTL={self.ttl_seconds}s)")
            self.close_stream(thread_id)
    
    async def consume_events(self, thread_id: str):
        """消费事件（前端 SSE 连接时调用）"""
        context = self.streams.get(thread_id)
        if not context:
            raise StreamNotFoundError(thread_id)
        
        # 取消 TTL 定时器（前端已连接）
        if context.ttl_task:
            context.ttl_task.cancel()
            context.ttl_task = None
        
        context.status = "streaming"
        
        while True:
            event = await context.queue.get()
            yield event
            
            # 终结事件后退出
            if event.type in ("complete", "interrupt", "error"):
                break
        
        self.close_stream(thread_id)
```

**交互时序**：

```
时间轴 →

POST /chat                          GET /stream/{thread_id}
    │                                      │
    ▼                                      │
[创建 StreamContext]                       │
[启动 TTL 定时器 (30s)]                    │
    │                                      │
    ▼                                      │
[启动 graph.astream()]                     │
    │                                      │
    ▼                                      ▼
[push metadata 事件到队列]         [连接建立, 取消 TTL 定时器]
    │                                      │
    ▼                                      ▼
[push stream 事件到队列]  ────────► [消费并推送 SSE]
    │                                      │
    ▼                                      ▼
[push complete 事件]      ────────► [推送后关闭连接]
    │                                      │
    ▼                                      │
[close_stream()]                           │
```

---

## 7. 执行流程

### 7.1 发送消息流程

```
┌────────────┐     ┌────────────┐     ┌──────────────┐     ┌────────────┐
│  Frontend  │     │  API Layer │     │ StreamManager│     │ Controller │
└─────┬──────┘     └─────┬──────┘     └──────┬───────┘     └─────┬──────┘
      │                  │                   │                   │
      │  POST /chat      │                   │                   │
      │─────────────────►│                   │                   │
      │                  │                   │                   │
      │                  │  create_stream    │                   │
      │                  │──────────────────►│                   │
      │                  │                   │                   │
      │                  │  启动后台任务      │                   │
      │                  │───────────────────│──────────────────►│
      │                  │                   │                   │
      │  返回 stream_url │                   │                   │
      │◄─────────────────│                   │                   │
      │                  │                   │                   │
      │  GET /stream     │                   │                   │
      │─────────────────►│                   │                   │
      │                  │                   │                   │
      │                  │  consume_events   │                   │
      │                  │──────────────────►│                   │
      │                  │                   │                   │
      │                  │                   │  事件推送         │
      │                  │                   │◄──────────────────│
      │                  │                   │                   │
      │  SSE events      │◄──────────────────│                   │
      │◄─────────────────│                   │                   │
      │                  │                   │                   │
```

### 7.2 权限确认流程（含 SSE 生命周期）🆕

```
┌────────────┐     ┌────────────┐     ┌──────────────┐
│  Frontend  │     │  API Layer │     │ StreamManager│
└─────┬──────┘     └─────┬──────┘     └──────┬───────┘
      │                  │                   │
      │  SSE 连接中...    │                   │
      │◄────────────────►│                   │
      │                  │                   │
      │  收到 interrupt 事件                  │
      │◄─────────────────│                   │
      │                  │                   │
      │                  │  close_stream     │
      │                  │──────────────────►│
      │                  │                   │
      │  SSE 连接关闭 ✂️  │                   │
      │◄ ─ ─ ─ ─ ─ ─ ─ ─│                   │
      │                  │                   │
      │  [显示确认对话框] │                   │
      │                  │                   │
      │  用户点击确认/拒绝│                   │
      │                  │                   │
      │  POST /resume    │                   │
      │─────────────────►│                   │
      │                  │                   │
      │                  │  create_stream    │
      │                  │──────────────────►│
      │                  │                   │
      │  返回新 stream_url                    │
      │◄─────────────────│                   │
      │                  │                   │
      │  销毁旧 EventSource                   │
      │  建立新 EventSource                   │
      │                  │                   │
      │  GET /stream (新连接)                 │
      │─────────────────►│                   │
      │                  │                   │
      │  继续接收后续事件 │                   │
      │◄─────────────────│                   │
```

**关键点**：
1. 收到 `interrupt` 事件后，**服务端主动关闭 SSE 连接**
2. 前端调用 `/resume` 后获得**新的** `stream_url`
3. 前端必须**销毁旧的 EventSource 实例**后再建立新连接
4. 这避免了 SSE 连接在等待用户操作期间长时间挂起

---

## 8. 实施步骤

### Phase 1: 基础框架（预计 1-2 天）

1. **搭建 FastAPI 应用**
   - 创建 `api/main.py`
   - 配置 CORS
   - 创建基础路由结构

2. **实现依赖注入**
   - `get_controller()`
   - `get_artifact_manager()`
   - `get_stream_manager()` 🆕

3. **定义 Schemas**
   - 请求模型
   - 响应模型

### Phase 2: REST API（预计 2-3 天）

1. **实现 Chat 路由**
   - POST /chat
   - GET /chat
   - GET /chat/{id}
   - DELETE /chat/{id}
   - POST /chat/{id}/resume

2. **实现 Artifact 路由**
   - GET /artifacts/{session_id}
   - GET /artifacts/{session_id}/{id}
   - GET /artifacts/{session_id}/{id}/versions
   - GET /artifacts/{session_id}/{id}/versions/{v}

3. **编写 API 测试**
   - 使用 pytest + httpx (AsyncClient)

### Phase 3: SSE 流式（预计 2-3 天）

1. **实现 StreamManager** 🆕
   - 事件缓冲队列
   - TTL 机制
   - 连接状态追踪

2. **实现 Stream 路由**
   - GET /stream/{thread_id}
   - 与 Controller 集成
   - 终结事件后主动关闭连接 🆕

3. **处理边缘情况**
   - 连接断开重连
   - 超时处理
   - TTL 过期清理 🆕

### Phase 4: 集成测试（预计 1-2 天）

1. **端到端测试**
   - 完整的对话流程
   - 权限确认流程（含连接关闭/重建）🆕
   - 分支对话流程

2. **性能测试**
   - 并发连接数
   - SSE 推送延迟
   - 内存泄漏检测（TTL 机制验证）🆕

---

## 9. 后续扩展预留

### 9.1 用户认证（Phase 2）

**预留位置**：
- `dependencies.py` 中的 `get_current_user()`
- 所有路由的 `user_id` 参数

**实现方式**（建议）：
- JWT Token 认证
- 可选：OAuth2 (Google/GitHub 登录)

**迁移路径**：
1. 添加 `users` 表和 Repository
2. 实现 JWT 生成/验证
3. 实现 `get_current_user()` 依赖
4. 添加 `/auth` 路由

### 9.2 WebSocket（如需双向通信）

**预留位置**：
- `api/routers/ws.py`

**使用场景**：
- 协作编辑
- 实时通知
- 双向控制（如取消执行）

---

## 10. 依赖清单

```txt
# API 依赖
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
sse-starlette>=1.8.0
pydantic>=2.5.0
python-multipart>=0.0.6  # 文件上传支持（预留）
aiofiles>=23.2.0         # 🆕 异步文件操作

# 测试依赖
httpx>=0.26.0
pytest-asyncio>=0.23.0
```

---

## 11. 配置项

```python
# api/config.py

class APIConfig:
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # CORS 配置
    CORS_ORIGINS: list = ["http://localhost:3000"]  # Next.js 开发服务器
    
    # SSE 配置
    SSE_PING_INTERVAL: int = 15  # 秒，保持连接活跃
    STREAM_TIMEOUT: int = 300    # 秒，最大执行时间
    STREAM_TTL: int = 30         # 🆕 秒，队列 TTL（前端未连接时自动清理）
    
    # 分页默认值
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100
```

---

## 附录：v1.0 → v1.1 变更摘要

| 章节 | 变更类型 | 说明 |
|------|---------|------|
| 2.1 | 🆕 新增 | 全链路异步 I/O 开发标准 |
| 3.1 | 更新 | 架构图增加 StreamManager |
| 4.1 | 更新 | resume 接口说明增强 |
| 4.3 | 更新 | SSE 连接生命周期表 |
| 5.1 | 更新 | 新增 services/stream_manager.py |
| 6.2 | 更新 | 依赖注入示例代码 |
| 6.4 | 🆕 新增 | StreamManager 详细设计 |
| 7.2 | 🆕 重写 | 权限确认流程含 SSE 生命周期 |
| 8 | 更新 | 实施步骤增加 StreamManager 相关任务 |
| 10 | 更新 | 依赖增加 aiofiles |
| 11 | 更新 | 配置增加 STREAM_TTL |
