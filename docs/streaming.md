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

| 事件类型 | 来源 | 说明 | data 主要字段 |
|----------|------|------|----------|
| `metadata` | Controller | 初始元数据 | `{conversation_id, thread_id, message_id}` |
| `agent_start` | Agent | Agent 开始执行 | `{success, content, metadata}` |
| `llm_chunk` | Agent | LLM 流式输出片段 | `{success, content*, reasoning_content, metadata, token_usage}` |
| `llm_complete` | Agent | LLM 单次调用完成 | `{success, content, reasoning_content, metadata, token_usage}` |
| `agent_complete` | Agent | Agent 单轮完成 | `{success, content, routing, metadata, token_usage}` |
| `tool_start` | Graph | 工具开始执行 | `{params}` |
| `tool_complete` | Graph | 工具执行完成 | `{success, duration_ms, error}` |
| `permission_request` | Graph | 请求权限确认 | `{permission_level, params}` |
| `permission_result` | Graph | 权限确认结果 | `{approved}` |
| `complete` | Controller | 执行完成 | `{success, interrupted, response, execution_metrics, ...}` |
| `error` | Controller | 执行错误 | `{success, error, conversation_id, ...}` |

**注意：** `llm_chunk.content` 是**累积**内容，非增量 delta。详细字段说明见 [API Reference](./api.md#stream-api)。

### 事件时序示例

```mermaid
sequenceDiagram
    participant S as Server
    participant C as Client

    S->>C: metadata
    S->>C: agent_start (lead_agent)
    Note right of C: content 是累积的
    S->>C: llm_chunk {content: "让我"}
    S->>C: llm_chunk {content: "让我来分析"}
    S->>C: llm_chunk {content: "让我来分析这个问题"}
    S->>C: llm_complete
    S->>C: agent_complete {routing: tool_call}

    S->>C: tool_start (web_search)
    Note over S: 执行工具
    S->>C: tool_complete {success, duration_ms}

    S->>C: agent_start (lead_agent)
    Note right of C: 新一轮，content 重新开始
    S->>C: llm_chunk {content: "根据搜索结果..."}
    S->>C: llm_complete
    S->>C: agent_complete {routing: null}

    S->>C: complete {response, execution_metrics}
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

> 完整的事件缓冲时序图见 [Request Lifecycle — 事件缓冲](request-lifecycle.md#phase-4-事件流与-sse)。

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

data: {"type":"agent_start","timestamp":"...","agent":"lead_agent","data":{"success":true,"content":"","metadata":{...}}}

data: {"type":"llm_chunk","timestamp":"...","agent":"lead_agent","data":{"success":true,"content":"让我","metadata":{...}}}

data: {"type":"llm_chunk","timestamp":"...","agent":"lead_agent","data":{"success":true,"content":"让我来分析","metadata":{...}}}

data: {"type":"llm_complete","timestamp":"...","agent":"lead_agent","data":{"success":true,"content":"让我来分析...","token_usage":{...}}}

data: {"type":"agent_complete","timestamp":"...","agent":"lead_agent","data":{"success":true,"content":"...","routing":{"type":"tool_call","tool_name":"web_search","params":{...}}}}

data: {"type":"tool_start","timestamp":"...","agent":"lead_agent","tool":"web_search","data":{"params":{"query":"Python async"}}}

data: {"type":"tool_complete","timestamp":"...","agent":"lead_agent","tool":"web_search","data":{"success":true,"duration_ms":1234,"error":null}}

data: {"type":"complete","timestamp":"...","data":{"success":true,"interrupted":false,"response":"...","execution_metrics":{...}}}
```

**关键点：**
- `llm_chunk.data.content` 是累积内容，每次事件包含从头开始的完整文本
- `tool_complete` 不包含工具返回的实际数据（出于性能考虑）
- `complete` 事件的 `interrupted` 字段指示是否需要用户确认权限

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
  const { thread_id, conversation_id, message_id, stream_url } = await response.json();

  // 2. 连接 SSE（使用返回的 stream_url）
  const eventSource = new EventSource(stream_url);

  // 3. 追踪状态
  let lastContent = '';  // 用于计算增量

  // 4. 统一处理消息，根据 type 分发
  eventSource.onmessage = (e) => {
    const event = JSON.parse(e.data);
    const { type, data, agent, tool } = event;

    switch (type) {
      case 'metadata':
        console.log('Started:', data);
        break;

      case 'llm_chunk':
        // data.content 是累积内容，计算增量
        const delta = data.content.slice(lastContent.length);
        lastContent = data.content;
        appendToOutput(delta);  // 流式显示增量
        break;

      case 'llm_complete':
        // LLM 单次调用完成，可以获取 token 统计
        console.log('Token usage:', data.token_usage);
        break;

      case 'agent_complete':
        // Agent 单轮完成，检查是否有后续操作
        if (data.routing) {
          console.log('Agent routing:', data.routing);
        }
        // 重置累积内容（下一轮 LLM 调用会重新开始）
        lastContent = '';
        break;

      case 'tool_start':
        showToolIndicator(tool, data.params);
        break;

      case 'tool_complete':
        hideToolIndicator(tool);
        if (!data.success) showError(data.error);
        break;

      case 'permission_request':
        showPermissionDialog(tool, data.params, data.permission_level);
        break;

      case 'complete':
        if (data.interrupted) {
          // 权限中断，需要用户确认
          showInterruptDialog(data.interrupt_data);
        } else {
          // 正常完成
          finalizeOutput(data.response);
          showMetrics(data.execution_metrics);
        }
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
interface Message {
  role: 'user' | 'assistant';
  content: string;
}

function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentContent, setCurrentContent] = useState('');
  const [pendingPermission, setPendingPermission] = useState<any>(null);
  const lastContentRef = useRef('');  // 追踪累积内容

  const sendMessage = async (content: string) => {
    setIsStreaming(true);
    setCurrentContent('');
    lastContentRef.current = '';

    // 添加用户消息
    setMessages(prev => [...prev, { role: 'user', content }]);

    const res = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content })
    });
    const { stream_url, conversation_id, message_id, thread_id } = await res.json();

    const eventSource = new EventSource(stream_url);

    eventSource.onmessage = (e) => {
      const event = JSON.parse(e.data);

      if (event.type === 'llm_chunk') {
        // data.content 是累积内容，直接使用
        setCurrentContent(event.data.content);
        lastContentRef.current = event.data.content;
      } else if (event.type === 'agent_complete') {
        // Agent 单轮完成，重置累积追踪
        lastContentRef.current = '';
      } else if (event.type === 'complete') {
        if (event.data.interrupted) {
          // 权限中断
          setPendingPermission({
            conversationId: conversation_id,
            threadId: thread_id,
            messageId: message_id,
            interruptData: event.data.interrupt_data
          });
        } else {
          // 正常完成
          setMessages(prev => [...prev, { role: 'assistant', content: event.data.response }]);
        }
        setCurrentContent('');
        setIsStreaming(false);
        eventSource.close();
      } else if (event.type === 'error') {
        setIsStreaming(false);
        eventSource.close();
      }
    };
  };

  const handlePermission = async (approved: boolean) => {
    if (!pendingPermission) return;

    const res = await fetch(`/api/v1/chat/${pendingPermission.conversationId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thread_id: pendingPermission.threadId,
        message_id: pendingPermission.messageId,
        approved
      })
    });

    const { stream_url } = await res.json();
    setPendingPermission(null);
    setIsStreaming(true);

    // 重新连接 SSE 继续流式处理...
    const eventSource = new EventSource(stream_url);
    // ... (同上处理逻辑)
  };

  return { messages, currentContent, isStreaming, pendingPermission, sendMessage, handlePermission };
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

权限中断有两种通知方式：
1. `permission_request` 事件 - 在中断前发送，包含工具信息
2. `complete` 事件 (`interrupted=true`) - 执行暂停，包含完整中断数据

```javascript
// 在 onmessage 处理器中
let pendingPermission = null;

// 方式1：收到 permission_request 时保存信息
if (event.type === 'permission_request') {
  const { tool, data } = event;
  pendingPermission = {
    tool,
    permissionLevel: data.permission_level,
    params: data.params
  };
}

// 方式2：收到 complete 且 interrupted=true 时处理
if (event.type === 'complete' && event.data.interrupted) {
  const { interrupt_data, thread_id, message_id, conversation_id } = event.data;

  // 显示确认对话框
  const approved = await showConfirmDialog({
    title: `确认执行 ${interrupt_data.tool_name}`,
    message: interrupt_data.message,
    details: interrupt_data.params
  });

  // 发送恢复请求
  const res = await fetch(`/api/v1/chat/${conversation_id}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      thread_id,
      message_id,
      approved
    })
  });

  // 重新连接 SSE 继续接收事件
  const { stream_url } = await res.json();
  const newEventSource = new EventSource(stream_url);
  // ... 绑定事件处理器
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
