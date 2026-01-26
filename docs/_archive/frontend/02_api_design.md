# ArtifactFlow API 层实现方案

> 版本: v1.5 | 依赖: 持久化改造完成
>
> **v1.5 更新**：
> - 新增 Section 4.4：统一的 StreamEventType 事件类型设计
> - 新增 Section 4.5：ExecutionMetrics 可观测性指标设计
> - 更新 Section 4.3：流式接口事件格式更新
>
> **v1.4 更新**：
> - 更新 Section 6.2：Checkpointer 从 MemorySaver 改为 AsyncSqliteSaver
> - 更新 Section 6.5：并发安全表更新 Checkpointer 说明
> - `create_multi_agent_graph` 改为 async 函数
>
> **v1.3 更新**：
> - 更新 Section 4.1：Resume 接口改为无状态设计（需要传入 `message_id`）
>
> **v1.2 更新**：
> - 新增 Section 6.5：数据库会话与事务管理（并发安全设计）
> - 更新 Section 6.2：完整的依赖注入示例（含请求级别 session 隔离）
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
  "message_id": "msg-xxx",
  "approved": true
}
```

**Response**:
```json
{
  "stream_url": "/api/v1/stream/thd-xxx"  // 新的 stream URL
}
```

**重要说明**：
- **必须参数**：`thread_id`、`message_id`、`approved`（这三个参数都可以从中断事件的返回数据中获取）
- **无状态设计**：Controller 不保存中断状态，resume 时必须传入完整参数
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

**Event Format（统一格式）**:

所有事件使用统一的 `StreamEventType` 格式：

```json
{
  "type": "agent_start | llm_chunk | llm_complete | agent_complete | tool_start | tool_complete | permission_request | permission_result | metadata | complete | error",
  "timestamp": "2024-01-19T10:00:00.000Z",
  "agent": "lead_agent",       // 可选，agent 相关事件
  "tool": "web_search",        // 可选，tool 相关事件
  "data": {...}                // 事件数据
}
```

**完整事件流示例**:

```
// 1. 会话开始（Controller 层）
data: {"type": "metadata", "timestamp": "...", "data": {"conversation_id": "conv-xxx", "message_id": "msg-xxx", "thread_id": "thd-xxx"}}

// 2. Agent 开始执行（Agent 层）
data: {"type": "agent_start", "timestamp": "...", "agent": "lead_agent", "data": {...}}

// 3. LLM 流式输出（Agent 层）
data: {"type": "llm_chunk", "timestamp": "...", "agent": "lead_agent", "data": {"content": "我需要", "reasoning_content": "..."}}
data: {"type": "llm_chunk", "timestamp": "...", "agent": "lead_agent", "data": {"content": "我需要搜索", "reasoning_content": "..."}}

// 4. LLM 完成（Agent 层）
data: {"type": "llm_complete", "timestamp": "...", "agent": "lead_agent", "data": {"token_usage": {"input_tokens": 100, "output_tokens": 50}}}

// 5. Agent 完成（可能带 routing）（Agent 层）
data: {"type": "agent_complete", "timestamp": "...", "agent": "lead_agent", "data": {"routing": {"type": "subagent", "target": "search_agent"}}}

// 6. 工具开始执行（Graph 层）
data: {"type": "tool_start", "timestamp": "...", "agent": "search_agent", "tool": "web_search", "data": {"params": {...}}}

// 7. 工具执行完成（Graph 层）
data: {"type": "tool_complete", "timestamp": "...", "agent": "search_agent", "tool": "web_search", "data": {"success": true, "duration_ms": 450}}

// 8. 权限请求（Graph 层，如果需要）
data: {"type": "permission_request", "timestamp": "...", "agent": "crawl_agent", "tool": "web_fetch", "data": {"permission_level": "confirm", "params": {...}}}

// 9. 最终完成（Controller 层，包含 execution_metrics）
data: {"type": "complete", "timestamp": "...", "data": {"success": true, "interrupted": false, "response": "...", "execution_metrics": {...}}}
```

**🆕 SSE 连接生命周期**：

| 事件类型 | 连接状态 | 产生位置 | 说明 |
|---------|---------|---------|------|
| `metadata` | 保持 | Controller | 首个事件，确认连接成功 |
| `agent_start` | 保持 | Agent | Agent 开始执行 |
| `llm_chunk` | 保持 | Agent | LLM 流式输出 |
| `llm_complete` | 保持 | Agent | LLM 单次调用完成 |
| `agent_complete` | 保持 | Agent | Agent 本轮完成（可能带 routing） |
| `tool_start` | 保持 | Graph | 工具开始执行 |
| `tool_complete` | 保持 | Graph | 工具执行完成 |
| `permission_request` | 保持 | Graph | 请求权限确认 |
| `permission_result` | 保持 | Graph | 权限确认结果 |
| `complete` | **关闭** | Controller | 正常完成/中断，服务端主动关闭连接 |
| `error` | **关闭** | Controller | 发生错误，服务端主动关闭连接 |

**设计要点**：
- 使用统一的 `StreamEventType`，各层（Agent/Graph/Controller）共用
- 事件直接透传，不再包装为 `stream` 事件
- `complete` / `error` 事件后，**服务端主动关闭 SSE 连接**
- `complete` 事件包含完整的 `execution_metrics` 可观测性数据

### 4.4 统一事件类型（StreamEventType）🆕

所有层使用统一的事件类型枚举：

```python
class StreamEventType(Enum):
    """
    统一的执行事件类型

    产生位置：
    - [Controller] 会话级别元数据和最终结果
    - [Agent]      LLM 执行相关事件
    - [Graph]      工具执行和权限相关事件
    """

    # ========== Controller 层 ==========
    METADATA = "metadata"                # 会话元数据
    COMPLETE = "complete"                # 整体完成（含 execution_metrics）
    ERROR = "error"                      # 错误

    # ========== Agent 层 ==========
    AGENT_START = "agent_start"          # agent 开始执行
    LLM_CHUNK = "llm_chunk"              # LLM token 流
    LLM_COMPLETE = "llm_complete"        # LLM 单次调用完成
    AGENT_COMPLETE = "agent_complete"    # agent 本轮完成

    # ========== Graph 层 ==========
    TOOL_START = "tool_start"            # 工具开始执行
    TOOL_COMPLETE = "tool_complete"      # 工具执行完成
    PERMISSION_REQUEST = "permission_request"  # 请求权限确认
    PERMISSION_RESULT = "permission_result"    # 权限确认结果
```

**前端状态推断**：

| 收到事件 | 前端显示 |
|---------|---------|
| `agent_start` | "Agent X 正在思考..." |
| `llm_chunk` | 流式显示 LLM 输出内容 |
| `tool_start` | "正在执行 web_search..." |
| `tool_complete` | 显示工具执行结果 |
| `permission_request` | 弹出权限确认对话框 |
| `complete` | 显示最终结果和 metrics |

### 4.5 ExecutionMetrics 可观测性指标 🆕

`complete` 事件包含完整的执行指标，用于前端展示执行统计信息。

**数据结构**：

```typescript
interface ExecutionMetrics {
  started_at: string;                    // ISO timestamp
  completed_at: string;                  // ISO timestamp
  total_duration_ms: number;             // 总耗时（毫秒）

  agent_executions: AgentExecutionRecord[];  // Agent 执行记录列表
  tool_calls: ToolCallRecord[];              // 工具调用记录列表
}

interface AgentExecutionRecord {
  agent_name: string;                    // Agent 名称
  model: string;                         // 使用的模型
  token_usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  llm_duration_ms: number;               // LLM 响应耗时
  started_at: string;
  completed_at: string;
}

interface ToolCallRecord {
  tool_name: string;                     // 工具名称
  success: boolean;                      // 是否成功
  duration_ms: number;                   // 执行耗时
  called_at: string;
  completed_at: string;
  agent: string;                         // 调用方 Agent
}
```

**示例数据**：

```json
{
  "execution_metrics": {
    "started_at": "2024-01-19T10:00:00.000Z",
    "completed_at": "2024-01-19T10:00:03.500Z",
    "total_duration_ms": 3500,

    "agent_executions": [
      {
        "agent_name": "lead_agent",
        "model": "qwen-plus",
        "token_usage": {"input_tokens": 1200, "output_tokens": 350, "total_tokens": 1550},
        "llm_duration_ms": 1200,
        "started_at": "2024-01-19T10:00:00.100Z",
        "completed_at": "2024-01-19T10:00:01.300Z"
      },
      {
        "agent_name": "search_agent",
        "model": "qwen-plus",
        "token_usage": {"input_tokens": 800, "output_tokens": 200, "total_tokens": 1000},
        "llm_duration_ms": 900,
        "started_at": "2024-01-19T10:00:01.400Z",
        "completed_at": "2024-01-19T10:00:02.300Z"
      },
      {
        "agent_name": "lead_agent",
        "model": "qwen-plus",
        "token_usage": {"input_tokens": 1500, "output_tokens": 400, "total_tokens": 1900},
        "llm_duration_ms": 600,
        "started_at": "2024-01-19T10:00:02.800Z",
        "completed_at": "2024-01-19T10:00:03.400Z"
      }
    ],

    "tool_calls": [
      {
        "tool_name": "web_search",
        "success": true,
        "duration_ms": 450,
        "called_at": "2024-01-19T10:00:02.350Z",
        "completed_at": "2024-01-19T10:00:02.800Z",
        "agent": "search_agent"
      }
    ]
  }
}
```

**前端使用建议**：

1. **Token 统计**：遍历 `agent_executions` 聚合各 Agent 的 token 使用量
2. **耗时分析**：展示 `total_duration_ms` 和各步骤耗时
3. **工具调用**：展示 `tool_calls` 中的成功/失败状态

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
- 提供 `get_db_session()` 依赖（请求级别的数据库会话）🆕
- 提供 `get_controller()` 依赖
- 提供 `get_artifact_manager()` 依赖
- 提供 `get_stream_manager()` 依赖
- 预留 `get_current_user()` 依赖

**设计要点**：
- 使用 FastAPI 的 `Depends` 系统
- **每个请求获得独立的数据库 session**（请求结束时自动 commit/rollback）🆕
- Controller 和 Manager 通过构造函数注入，绑定到请求的 session
- 用户依赖预留为返回 `None`（无认证时）

**示例代码**：
```python
# dependencies.py
from functools import lru_cache
from typing import AsyncGenerator, Any
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.controller import ExecutionController
from core.conversation_manager import ConversationManager
from core.graph import create_multi_agent_graph, create_async_sqlite_checkpointer
from tools.implementations.artifact_ops import ArtifactManager
from db.database import DatabaseManager
from repositories.artifact_repo import ArtifactRepository
from repositories.conversation_repo import ConversationRepository
from api.services.stream_manager import StreamManager

# ============================================================
# 全局单例（跨请求共享）
# ============================================================

_db_manager: DatabaseManager = None
_checkpointer: Any = None  # AsyncSqliteSaver，LangGraph 状态持久化

async def init_globals():
    """应用启动时初始化"""
    global _db_manager, _checkpointer
    _db_manager = DatabaseManager()
    await _db_manager.initialize()
    # 创建共享的 checkpointer（用于 interrupt/resume）
    # 使用 AsyncSqliteSaver 持久化到 SQLite
    _checkpointer = await create_async_sqlite_checkpointer("data/langgraph.db")

async def close_globals():
    """应用关闭时清理"""
    global _db_manager, _checkpointer
    # 关闭 checkpointer 的 aiosqlite 连接
    if _checkpointer and hasattr(_checkpointer, 'conn'):
        await _checkpointer.conn.close()
    if _db_manager:
        await _db_manager.close()

@lru_cache()
def get_stream_manager() -> StreamManager:
    return StreamManager(ttl_seconds=30)

def get_checkpointer() -> Any:
    return _checkpointer

# ============================================================
# 请求级别依赖（每个请求独立）
# ============================================================

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    每个请求获得独立的数据库 session

    请求成功 → 自动 commit
    请求失败 → 自动 rollback
    """
    async with _db_manager.session() as session:
        yield session

async def get_artifact_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ArtifactManager:
    """每个请求获得独立的 ArtifactManager（绑定到请求的 session）"""
    repo = ArtifactRepository(session)
    return ArtifactManager(repo)

async def get_conversation_manager(
    session: AsyncSession = Depends(get_db_session)
) -> ConversationManager:
    """每个请求获得独立的 ConversationManager（绑定到请求的 session）"""
    repo = ConversationRepository(session)
    return ConversationManager(repo)

async def get_controller(
    artifact_manager: ArtifactManager = Depends(get_artifact_manager),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
) -> ExecutionController:
    """
    每个请求获得独立的 Controller

    注意：
    - Graph 每次创建新实例，因为它持有 artifact_manager 引用
    - 但 checkpointer 是共享的，以支持跨请求的 interrupt/resume
    - create_multi_agent_graph 是 async 函数
    """
    compiled_graph = await create_multi_agent_graph(
        artifact_manager=artifact_manager,
        checkpointer=get_checkpointer()  # 使用共享的 checkpointer
    )
    return ExecutionController(
        compiled_graph,
        artifact_manager=artifact_manager,
        conversation_manager=conversation_manager
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

### 6.5 数据库会话与事务管理 🆕

> **核心原则**：每个 HTTP 请求使用独立的数据库 session，请求结束时自动 commit 或 rollback。

**为什么需要请求级别的 Session 隔离**：

1. **并发安全**：多个并发请求不会共享 session，避免数据竞争
2. **事务边界清晰**：一个请求 = 一个事务，要么全部成功，要么全部回滚
3. **资源及时释放**：请求结束后 session 自动关闭，避免连接泄漏

**依赖注入链路**：

```
HTTP Request
    │
    ▼
get_db_session()        # 创建独立的 AsyncSession
    │
    ├──► get_artifact_manager()     # 创建 ArtifactRepository → ArtifactManager
    │
    ├──► get_conversation_manager() # 创建 ConversationRepository → ConversationManager
    │
    └──► get_controller()           # 创建 Graph → ExecutionController
             │
             ▼
        执行请求处理
             │
             ▼
        请求成功 → session.commit()
        请求失败 → session.rollback()
             │
             ▼
        session.close()
```

**并发安全保证**：

| 组件 | 共享方式 | 说明 |
|------|---------|------|
| `DatabaseManager` | 全局单例 | 只管理连接池，不持有 session 状态 |
| `Checkpointer` | 全局单例 | AsyncSqliteSaver，LangGraph 状态持久化，支持 interrupt/resume |
| `AsyncSession` | 请求独立 | 每个请求创建新的数据库会话 |
| `Repository` | 请求独立 | 绑定到请求的 session |
| `Manager` | 请求独立 | 绑定到请求的 repository |
| `Graph` | 请求独立 | 持有 manager 引用，每个请求创建新实例（但共享 checkpointer） |
| `Controller` | 请求独立 | 绑定到请求的 managers |
| `StreamManager` | 全局单例 | 无数据库操作，可安全共享 |

**注意事项**：

1. **Graph 每次请求创建新实例**：因为 Graph 内的工具持有 `artifact_manager` 引用，如果共享 Graph 实例，并发请求会互相覆盖 manager 的 repository
2. **Graph 创建开销**：每次请求创建 Graph 有一定开销，但相比 LLM 推理时间可以忽略
3. **如需优化**：可以考虑使用 `contextvars` 实现请求上下文隔离，但会增加复杂度

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

## 附录：v1.4 → v1.5 变更摘要

| 章节 | 变更类型 | 说明 |
|------|---------|------|
| 4.3 | 🔄 更新 | 流式接口事件格式更新为统一的 StreamEventType |
| 4.4 | 🆕 新增 | 统一事件类型（StreamEventType）设计 |
| 4.5 | 🆕 新增 | ExecutionMetrics 可观测性指标设计 |

**关键变更说明**：

1. **统一事件类型**：移除 `ControllerEventType`，所有层（Agent/Graph/Controller）使用统一的 `StreamEventType`
2. **事件直接透传**：Controller 不再将事件包装为 `stream` 类型，直接透传 graph 的事件
3. **ExecutionMetrics**：新增可观测性指标，在 `complete` 事件中返回完整的执行统计信息
4. **事件命名变更**：
   - `start` → `agent_start`
   - `complete`（Agent 层）→ `agent_complete`
   - `tool_result` → `tool_complete`
   - `permission_required` → `permission_request`
5. **新增事件**：`tool_start`（工具开始执行）、`permission_result`（权限确认结果）

---

## 附录：v1.3 → v1.4 变更摘要

| 章节 | 变更类型 | 说明 |
|------|---------|------|
| 6.2 | 🔄 更新 | Checkpointer 从 MemorySaver 改为 AsyncSqliteSaver |
| 6.5 | 🔄 更新 | 并发安全表更新 Checkpointer 说明 |

**关键变更说明**：

1. **Checkpointer 持久化**：从内存存储 (`MemorySaver`) 改为 SQLite 持久化 (`AsyncSqliteSaver`)，服务重启后 interrupt/resume 状态不丢失
2. **`create_multi_agent_graph` 改为 async**：因为创建 checkpointer 需要异步初始化
3. **连接清理**：`close_globals()` 需要关闭 checkpointer 的 aiosqlite 连接，避免程序无法正常退出

---

## 附录：v1.2 → v1.3 变更摘要

| 章节 | 变更类型 | 说明 |
|------|---------|------|
| 4.1 | 🔄 更新 | Resume 接口增加 `message_id` 参数，改为无状态设计 |
| 6.2 | 🔄 更新 | 依赖注入示例增加共享 checkpointer |
| 6.5 | 🔄 更新 | 并发安全表增加 Checkpointer 组件 |

**关键变更说明**：

1. **Controller 无状态设计**：Controller 不再保存 `interrupted_threads` 状态
2. **Resume 接口变更**：必须传入 `thread_id`、`message_id`、`approved` 三个参数
3. **参数来源**：所有 resume 所需参数都可以从中断事件（`interrupt`）的返回数据中获取
4. **Checkpointer 共享**：LangGraph 的 checkpointer 必须跨请求共享，否则 interrupt/resume 无法正常工作

---

## 附录：v1.1 → v1.2 变更摘要

| 章节 | 变更类型 | 说明 |
|------|---------|------|
| 6.2 | 🔄 重写 | 完整的依赖注入示例，包含请求级别 session 隔离 |
| 6.5 | 🆕 新增 | 数据库会话与事务管理（并发安全设计） |

**关键变更说明**：

1. **Controller 不再管理事务**：事务边界由 API 层的依赖注入管理
2. **每个请求独立的组件链**：Session → Repository → Manager → Graph → Controller
3. **Graph 每次请求创建新实例**：因为 Graph 持有 artifact_manager 引用，共享会导致并发问题

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
