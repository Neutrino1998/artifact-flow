# 流式事件系统

ArtifactFlow 使用 Server-Sent Events (SSE) 实现实时事件推送，让前端能够即时展示执行进度。

## 架构概览

```mermaid
flowchart TB
    subgraph Graph["Graph 执行"]
        Agent["Agent Node"]
        Tool["Tool Node"]
    end

    subgraph Events["事件产生"]
        E1["LLM_CHUNK"]
        E2["AGENT_COMPLETE"]
        E3["TOOL_START"]
        E4["TOOL_COMPLETE"]
    end

    subgraph Pipeline["事件管道"]
        Controller["Controller"]
        SM["StreamManager<br/>(Event Queue)"]
        SSE["SSE 端点"]
    end

    Agent --> E1
    Agent --> E2
    Tool --> E3
    Tool --> E4

    E1 & E2 & E3 & E4 --> Controller
    Controller --> SM
    SM --> SSE
    SSE --> Client["Client"]
```

## 事件类型

### 完整事件列表

| 事件类型 | 来源 | 说明 | 数据结构 |
|----------|------|------|----------|
| `metadata` | Controller | 初始元数据 | `{conversation_id, thread_id, message_id}` |
| `agent_start` | Agent | Agent 开始执行 | `{agent}` |
| `llm_chunk` | Agent | LLM 流式输出片段 | `{content, agent}` |
| `llm_complete` | Agent | LLM 输出完成 | `{content, agent, token_usage}` |
| `agent_complete` | Agent | Agent 执行完成 | `{agent, response}` |
| `tool_start` | Graph | 工具开始执行 | `{tool, params}` |
| `tool_complete` | Graph | 工具执行完成 | `{tool, success, data, error}` |
| `permission_request` | Graph | 请求权限确认 | `{tool, params, permission}` |
| `permission_result` | Graph | 权限确认结果 | `{tool, approved}` |
| `complete` | Controller | 执行完成 | `{response, metrics}` |
| `error` | Controller | 执行错误 | `{error, traceback}` |

### 事件时序示例

```mermaid
sequenceDiagram
    participant S as Server
    participant C as Client

    S->>C: metadata
    S->>C: agent_start (lead_agent)
    S->>C: llm_chunk "让我"
    S->>C: llm_chunk "来分析"
    S->>C: llm_chunk "这个问题"
    S->>C: llm_chunk "<tool_call>..."
    S->>C: llm_complete

    S->>C: tool_start (web_search)
    Note over S: 执行工具
    S->>C: tool_complete

    S->>C: agent_start (lead_agent)
    S->>C: llm_chunk "..."
    S->>C: agent_complete

    S->>C: complete
```

## StreamManager

### 核心职责

1. **事件缓冲**：POST 请求返回后，Graph 继续执行产生的事件需要缓冲
2. **队列管理**：每个 `thread_id` 对应独立的事件队列
3. **TTL 清理**：防止前端未连接导致的内存泄漏

### 实现

```python
# src/api/services/stream_manager.py

@dataclass
class StreamContext:
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: datetime = field(default_factory=datetime.now)
    status: Literal["pending", "streaming", "closed"] = "pending"
    ttl_task: Optional[asyncio.Task] = None

class StreamManager:
    def __init__(self, ttl_seconds: int = 30):
        self.streams: Dict[str, StreamContext] = {}
        self.ttl_seconds = ttl_seconds

    async def create_stream(self, thread_id: str) -> StreamContext:
        """创建新的事件流"""
        context = StreamContext()
        self.streams[thread_id] = context

        # 启动 TTL 计时器
        context.ttl_task = asyncio.create_task(
            self._ttl_cleanup(thread_id)
        )

        return context

    async def push_event(self, thread_id: str, event: Dict[str, Any]) -> bool:
        """推送事件到队列"""
        context = self.streams.get(thread_id)
        if not context or context.status == "closed":
            return False
        await context.queue.put(event)
        return True

    async def consume_events(
        self,
        thread_id: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """消费事件流"""
        context = self.streams.get(thread_id)
        if not context:
            raise StreamNotFoundError(thread_id)

        # 取消 TTL 计时器
        if context.ttl_task:
            context.ttl_task.cancel()
            context.ttl_task = None

        context.status = "streaming"

        try:
            while True:
                event = await context.queue.get()
                yield event

                # 检查是否结束
                if event.get("type") in ("complete", "error"):
                    break
        finally:
            await self.close_stream(thread_id)

    async def _ttl_cleanup(self, thread_id: str):
        """TTL 超时清理"""
        await asyncio.sleep(self.ttl_seconds)

        context = self.streams.get(thread_id)
        if context and context.status == "pending":
            # 前端未连接，清理队列
            await self._close_stream_internal(thread_id)
```

### 时序图

```mermaid
sequenceDiagram
    participant Client
    participant POST /chat
    participant StreamManager
    participant Graph
    participant GET /stream

    Client->>POST /chat: 发送消息
    POST /chat->>StreamManager: create_stream(thread_id)
    POST /chat->>Client: 返回 {thread_id, ...}

    Note over StreamManager: TTL 计时器启动 (30s)

    POST /chat->>Graph: 后台执行
    Graph->>StreamManager: push_event(agent_start)
    Graph->>StreamManager: push_event(llm_chunk)

    Client->>GET /stream: 连接 SSE
    GET /stream->>StreamManager: consume_events(thread_id)
    Note over StreamManager: 取消 TTL 计时器

    StreamManager->>GET /stream: yield agent_start
    StreamManager->>GET /stream: yield llm_chunk

    Graph->>StreamManager: push_event(complete)
    StreamManager->>GET /stream: yield complete
    GET /stream->>Client: SSE: complete
    Note over StreamManager: 清理队列
```

## SSE 端点

### 实现

```python
# src/api/routers/stream.py

@router.get("/{thread_id}")
async def stream_events(
    thread_id: str,
    stream_manager: StreamManager = Depends(get_stream_manager),
) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event in stream_manager.consume_events(thread_id):
                yield format_sse_event(event)

                # 检查是否是终结事件
                if event.get("type") in ("complete", "error"):
                    break

        except StreamNotFoundError:
            error_event = {"type": "error", "data": {"error": f"Stream not found"}}
            yield format_sse_event(error_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )

# src/api/utils/sse.py
def format_sse_event(data: Dict[str, Any], event: str = None) -> str:
    """
    格式化为 SSE 协议

    Args:
        data: 事件数据（整个事件字典，包含 type 字段）
        event: SSE event 名称（可选，通常不使用）

    Returns:
        格式化的 SSE 字符串
    """
    lines = []
    if event:
        lines.append(f"event: {event}")
    json_data = json.dumps(data, ensure_ascii=False)
    lines.append(f"data: {json_data}")
    return "\n".join(lines) + "\n\n"
```

### SSE 协议格式

事件类型包含在 `data` 的 JSON 对象内（`type` 字段），而非使用 SSE 的 `event:` 字段：

```
data: {"type":"metadata","timestamp":"...","data":{"conversation_id":"abc","thread_id":"xyz","message_id":"123"}}

data: {"type":"agent_start","timestamp":"...","agent":"lead_agent"}

data: {"type":"llm_chunk","timestamp":"...","agent":"lead_agent","data":{"content":"让我"}}

data: {"type":"llm_chunk","timestamp":"...","agent":"lead_agent","data":{"content":"来分析"}}

data: {"type":"tool_start","timestamp":"...","tool":"web_search","data":{"params":{"query":"Python async"}}}

data: {"type":"tool_complete","timestamp":"...","tool":"web_search","data":{"success":true,"data":[...]}}

data: {"type":"complete","timestamp":"...","data":{"response":"...","metrics":{...}}}
```

## 前端集成

### JavaScript 示例

由于事件类型在 `data` JSON 内部，前端使用 `onmessage` 统一处理后根据 `type` 分发：

```javascript
async function chat(content) {
  // 1. 发送消息
  const response = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  });
  const { thread_id, conversation_id, message_id } = await response.json();

  // 2. 连接 SSE
  const eventSource = new EventSource(`/api/v1/stream/${thread_id}`);

  // 3. 统一处理消息，根据 type 分发
  eventSource.onmessage = (e) => {
    const event = JSON.parse(e.data);
    const { type, data, agent, tool } = event;

    switch (type) {
      case 'metadata':
        console.log('Started:', data);
        break;

      case 'llm_chunk':
        appendToOutput(data.content);  // 流式显示
        break;

      case 'tool_start':
        showToolIndicator(tool, data.params);
        break;

      case 'tool_complete':
        hideToolIndicator(tool);
        if (!data.success) showError(data.error);
        break;

      case 'permission_request':
        showPermissionDialog(tool, data.params, data.permission);
        break;

      case 'complete':
        finalizeOutput(data.response);
        showMetrics(data.metrics);
        eventSource.close();
        break;

      case 'error':
        showError(data.error);
        eventSource.close();
        break;
    }
  };

  eventSource.onerror = () => {
    eventSource.close();
  };
}
```

### React Hook 示例

```typescript
function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentContent, setCurrentContent] = useState('');

  const sendMessage = async (content: string) => {
    setIsStreaming(true);
    setCurrentContent('');

    // 添加用户消息
    setMessages(prev => [...prev, { role: 'user', content }]);

    const res = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content })
    });
    const { thread_id } = await res.json();

    const eventSource = new EventSource(`/api/v1/stream/${thread_id}`);

    eventSource.onmessage = (e) => {
      const event = JSON.parse(e.data);

      if (event.type === 'llm_chunk') {
        setCurrentContent(prev => prev + event.data.content);
      } else if (event.type === 'complete') {
        setMessages(prev => [...prev, { role: 'assistant', content: event.data.response }]);
        setCurrentContent('');
        setIsStreaming(false);
        eventSource.close();
      } else if (event.type === 'error') {
        setIsStreaming(false);
        eventSource.close();
      }
    };
  };

  return { messages, currentContent, isStreaming, sendMessage };
}
```

## 权限中断处理

当遇到需要确认的工具时：

### 后端流程

```python
# tool_execution_node 中
if tool.permission == ToolPermission.CONFIRM:
    # 发送 PERMISSION_REQUEST 事件
    writer({
        "type": StreamEventType.PERMISSION_REQUEST.value,
        "agent": from_agent,
        "tool": tool_name,
        "timestamp": datetime.now().isoformat(),
        "data": {
            "permission_level": tool.permission.value,
            "params": params
        }
    })

    # 中断执行，等待用户确认
    is_approved = interrupt({
        "type": "tool_permission",
        "agent": from_agent,
        "tool_name": tool_name,
        "params": params,
        "permission_level": tool.permission.value,
        "message": f"Tool '{tool_name}' requires {tool.permission.value} permission"
    })

    # 发送 PERMISSION_RESULT 事件
    writer({
        "type": StreamEventType.PERMISSION_RESULT.value,
        "agent": from_agent,
        "tool": tool_name,
        "timestamp": datetime.now().isoformat(),
        "data": {"approved": is_approved}
    })

    if not is_approved:
        # 用户拒绝，返回错误结果
        ...
```

### 前端处理

```javascript
// 在 onmessage 处理器中
if (event.type === 'permission_request') {
  const { tool, data } = event;
  const { permission_level, params } = data;

  // 显示确认对话框
  const approved = await showConfirmDialog({
    title: `确认执行 ${tool}`,
    message: `工具 '${tool}' 需要 ${permission_level} 权限`,
    details: params
  });

  // 发送恢复请求
  await fetch(`/api/v1/chat/${conversationId}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      thread_id: threadId,
      approved
    })
  });
}
```

## 配置选项

```python
# src/api/config.py

class APIConfig(BaseSettings):
    # SSE 配置
    SSE_PING_INTERVAL: int = 15      # 心跳间隔（秒）
    STREAM_TIMEOUT: int = 300        # 流超时（秒）
    STREAM_TTL: int = 30             # 队列 TTL（秒）

    class Config:
        env_prefix = "ARTIFACTFLOW_"
```

## 错误处理

### 连接断开

前端应处理连接断开情况：

```javascript
eventSource.onerror = (e) => {
  if (eventSource.readyState === EventSource.CLOSED) {
    // 连接已关闭
    handleDisconnect();
  } else {
    // 尝试重连（浏览器自动）
    showReconnecting();
  }
};
```

### 超时处理

```javascript
const timeout = setTimeout(() => {
  eventSource.close();
  showTimeout();
}, 5 * 60 * 1000);  // 5 分钟超时

eventSource.addEventListener('complete', () => {
  clearTimeout(timeout);
});
```
