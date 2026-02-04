# ArtifactFlow 前端实现方案

> 版本: v1.2 | 依赖: API 层完成
>
> **v1.1 变更**：整合架构优化建议，增强状态管理、明确渲染策略、规范 SSE 生命周期
>
> **v1.2 变更**：对齐后端 StreamEventType 事件协议，修正 SSE 事件分发、streamStore 更新语义、权限流程、Artifact 更新检测、折叠逻辑

## 1. 设计目标

构建类似 Claude Web UI 的双面板交互界面：

1. **左侧对话面板**：消息列表、实时流式输出、分支对话支持
2. **右侧 Artifact 面板**：Artifact 展示、版本切换、Diff 对比
3. **流式体验**：实时渲染 Agent 思考过程、工具调用状态
4. **历史加载**：加载历史对话时只显示最终结果，不渲染中间过程

---

## 2. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | Next.js 14 (App Router) | React 生态、SSR/SSG 支持、API Routes |
| 状态管理 | Zustand | 轻量、简单、TypeScript 友好 |
| UI 组件库 | shadcn/ui | 高质量、可定制、基于 Radix |
| 样式 | Tailwind CSS | 与 shadcn/ui 配套、快速开发 |
| 代码编辑器 | Monaco Editor | VS Code 同款、原生支持 Diff |
| Markdown 渲染 | react-markdown + remark-gfm | 轻量、插件丰富 |
| SSE 客户端 | 原生 EventSource | 浏览器内置、简单可靠 |

---

## 3. 整体架构

### 3.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│                         Header                               │
│  [Logo]  [New Chat]                    [Settings] [User]    │
├─────────────────────────────────────────────────────────────┤
│           │                                                  │
│  Sidebar  │                   Main Content                   │
│           │                                                  │
│  ┌─────┐  │  ┌──────────────────┬──────────────────────┐   │
│  │Conv │  │  │                  │                      │   │
│  │List │  │  │   Chat Panel     │   Artifact Panel     │   │
│  │     │  │  │                  │                      │   │
│  │     │  │  │  - Messages      │  - Tabs (artifacts)  │   │
│  │     │  │  │  - Input         │  - Content View      │   │
│  │     │  │  │  - Stream UI     │  - Version Selector  │   │
│  │     │  │  │                  │  - Monaco/Markdown   │   │
│  └─────┘  │  └──────────────────┴──────────────────────┘   │
│           │                                                  │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 路由设计

```
/                           → 重定向到 /chat
/chat                       → 新对话页面
/chat/[conversationId]      → 对话详情页面
/settings                   → 设置页面（预留）
/login                      → 登录页面（预留）
```

### 3.3 组件层次

```
app/
├── layout.tsx              # 根布局（Header + Sidebar）
├── page.tsx                # 首页重定向
│
├── chat/
│   ├── layout.tsx          # Chat 布局（双面板）
│   ├── page.tsx            # 新对话
│   └── [id]/
│       └── page.tsx        # 对话详情
│
└── (auth)/                 # 预留：认证相关
    ├── login/
    │   └── page.tsx
    └── register/
        └── page.tsx
```

---

## 4. 核心组件设计

### 4.1 Chat Panel 组件树

```
ChatPanel/
├── MessageList/
│   ├── MessageItem/
│   │   ├── UserMessage         # 用户消息气泡
│   │   ├── AssistantMessage    # AI 响应气泡
│   │   │   ├── StreamingContent    # 流式内容（打字机效果）
│   │   │   ├── ReasoningBlock      # 思考过程折叠块
│   │   │   └── ToolCallIndicator   # 工具调用指示器
│   │   └── BranchIndicator     # 分支标记
│   └── StreamingMessage        # 正在生成的消息
│
├── ChatInput/
│   ├── TextArea               # 输入框
│   ├── SendButton             # 发送按钮
│   └── AttachButton           # 附件按钮（预留）
│
└── PermissionDialog/          # 权限确认对话框
    ├── ToolInfo               # 工具信息展示
    └── ActionButtons          # 确认/拒绝按钮
```

### 4.2 Artifact Panel 组件树

```
ArtifactPanel/
├── ArtifactTabs/
│   ├── TabItem                # 单个 Artifact Tab
│   └── AddTabButton           # 预留：手动创建 Artifact
│
├── ArtifactViewer/
│   ├── ViewModeToggle         # 切换 Raw/Rendered 模式
│   ├── VersionSelector        # 版本选择下拉
│   │
│   ├── MonacoViewer/          # Raw 模式
│   │   ├── SingleView         # 单版本查看
│   │   └── DiffView           # Diff 对比
│   │
│   └── RenderedViewer/        # 渲染模式
│       ├── MarkdownRenderer   # Markdown 渲染
│       ├── CodeHighlighter    # 代码高亮
│       └── HtmlPreview        # HTML 预览（iframe sandbox）
│
└── ArtifactMeta/
    ├── Title                  # 标题
    ├── UpdateTime             # 更新时间
    └── VersionInfo            # 版本信息
```

### 4.3 Sidebar 组件树

```
Sidebar/
├── NewChatButton              # 新建对话按钮
│
├── ConversationList/
│   ├── ConversationItem/
│   │   ├── Title              # 对话标题
│   │   ├── Preview            # 最后消息预览
│   │   ├── Timestamp          # 时间戳
│   │   └── ActionMenu         # 删除、重命名等
│   └── LoadMoreButton         # 加载更多
│
└── BottomSection/
    ├── SettingsButton         # 设置按钮
    └── UserInfo               # 用户信息（预留）
```

---

## 5. 状态管理设计

### 5.1 Store 划分

```
stores/
├── conversationStore.ts      # 对话状态
├── artifactStore.ts          # Artifact 状态
├── streamStore.ts            # 流式状态
└── uiStore.ts                # UI 状态
```

### 5.2 conversationStore

**状态字段**：
```typescript
// [v1.1 新增] 消息状态枚举
type MessageStatus = 'sending' | 'sent' | 'error';

// [v1.1 新增] 增强的消息类型
interface Message {
  id: string;
  conversationId: string;
  parentId: string | null;
  content: string;
  role: 'user' | 'assistant';
  response?: string;          // assistant 的响应内容
  createdAt: string;
  
  // [v1.1 新增] 状态字段 - 用于乐观更新的安全回滚
  status: MessageStatus;
  
  // [v1.1 新增] 错误信息 - 当 status === 'error' 时存在
  error?: string;
}

interface ConversationStore {
  // 对话列表
  conversations: ConversationSummary[]
  isLoadingList: boolean
  hasMore: boolean
  
  // 当前对话
  currentConversation: ConversationDetail | null
  isLoadingConversation: boolean
  
  // 消息分支路径
  currentBranchPath: string[]  // 从根到当前节点的 message IDs
  
  // Actions
  fetchConversations(): Promise<void>
  fetchConversation(id: string): Promise<void>
  createConversation(): string
  deleteConversation(id: string): Promise<void>
  switchBranch(messageId: string): void
  
  // [v1.1 新增] 乐观更新相关 Actions
  addOptimisticMessage(message: Omit<Message, 'status'>): string  // 返回临时 ID
  confirmMessage(tempId: string, realId: string): void            // 确认成功
  failMessage(tempId: string, error: string): void                // 标记失败
  retryMessage(messageId: string): Promise<void>                  // 重试发送
  removeMessage(messageId: string): void                          // 移除失败消息
}
```

**[v1.1 新增] 乐观更新安全回滚机制**：

```typescript
// 发送消息时的乐观更新流程
async function sendMessage(content: string, parentId?: string) {
  // 1. 立即添加消息到 UI（status: 'sending'）
  const tempId = addOptimisticMessage({
    id: `temp-${Date.now()}`,
    content,
    role: 'user',
    parentId,
    // ...
  });
  
  try {
    // 2. 调用 API
    const response = await api.sendMessage(content, parentId);
    
    // 3. 成功：确认消息（status: 'sent'，替换 ID）
    confirmMessage(tempId, response.messageId);
    
  } catch (error) {
    // 4. 失败：标记错误（status: 'error'）
    failMessage(tempId, error.message);
    // UI 显示重试按钮，用户可选择重试或删除
  }
}
```

### 5.3 artifactStore

**状态字段**：
```typescript
interface ArtifactStore {
  // 当前 session 的 artifacts
  artifacts: ArtifactSummary[]
  isLoading: boolean
  
  // 当前选中的 artifact
  currentArtifact: ArtifactDetail | null
  currentVersion: number | null
  compareVersion: number | null  // Diff 对比的版本
  
  // 版本缓存
  versionCache: Map<string, ArtifactVersion>  // `${artifactId}-${version}` -> content
  
  // [v1.1 新增] 更新指示
  pendingUpdates: Set<string>  // 有待刷新的 artifact IDs
  
  // Actions
  fetchArtifacts(sessionId: string): Promise<void>
  fetchArtifact(sessionId: string, artifactId: string): Promise<void>
  fetchVersion(sessionId: string, artifactId: string, version: number): Promise<void>
  selectArtifact(artifactId: string): void
  selectVersion(version: number): void
  enableDiffMode(baseVersion: number): void
  disableDiffMode(): void
  
  // [v1.1 新增] 标记更新
  markPendingUpdate(artifactId: string): void
  clearPendingUpdates(): void
}
```

### 5.4 streamStore

**[v1.2 更新] 与后端 StreamEventType 对齐**

后端通过 SSE 发送的事件类型（`src/core/events.py` StreamEventType）：

| 层级 | 事件类型 | 说明 |
|------|----------|------|
| Controller | `metadata` | 会话元数据（conversation_id, thread_id） |
| Controller | `complete` | 整体完成（含 execution_metrics，interrupted 标记中断） |
| Controller | `error` | 错误 |
| Agent | `agent_start` | agent 开始执行 |
| Agent | `llm_chunk` | LLM token 流（**累积值，非增量**） |
| Agent | `llm_complete` | LLM 单次调用完成 |
| Agent | `agent_complete` | agent 本轮完成 |
| Graph | `tool_start` | 工具开始执行 |
| Graph | `tool_complete` | 工具执行完成（含 result.data） |
| Graph | `permission_request` | 请求权限确认 |
| Graph | `permission_result` | 权限确认结果 |

**[v1.2 重要] LLM_CHUNK 数据语义**：后端 `llm_chunk` 事件的 `data.content` 和 `data.reasoning_content` 是**累积值**（到当前为止的完整内容），不是增量 delta。前端必须使用 `setContent()` / `setReasoning()` 直接替换，而非 `append`。

**ToolCallInfo 类型定义**：
```typescript
// [v1.2 新增] 工具调用信息（匹配 tool_start / tool_complete 事件）
interface ToolCallInfo {
  toolName: string;
  agent: string;
  status: 'running' | 'success' | 'failed';
  params?: Record<string, any>;
  resultData?: any;       // [v1.2] 工具返回的实际数据（来自 tool_complete 事件）
  error?: string;
  durationMs?: number;
}
```

**状态字段**：
```typescript
interface StreamStore {
  // 当前流式状态
  isStreaming: boolean
  currentThreadId: string | null

  // [v1.2 更新] 流式内容（累积值，直接替换）
  streamContent: string
  reasoningContent: string

  // Agent 执行状态
  currentAgent: string | null
  toolCalls: ToolCallInfo[]

  // 权限请求
  pendingPermission: PermissionRequest | null

  // [v1.1 新增] EventSource 实例引用（用于生命周期管理）
  eventSourceRef: EventSource | null

  // Actions
  startStream(threadId: string): void
  setContent(content: string): void          // [v1.2 更新] 替换（非 append），因后端发送累积值
  setReasoning(reasoning: string): void      // [v1.2 更新] 替换（非 append），因后端发送累积值
  setCurrentAgent(agent: string): void       // [v1.2 新增] 从 agent_start 事件设置
  addToolStart(info: ToolCallInfo): void     // [v1.2 新增] 从 tool_start 事件添加 running 状态
  updateToolComplete(toolName: string, result: Partial<ToolCallInfo>): void  // [v1.2 新增] 从 tool_complete 更新状态
  requestPermission(request: PermissionRequest): void
  resolvePermission(approved: boolean): Promise<void>
  endStream(): void
  reset(): void

  // [v1.1 新增] EventSource 生命周期管理
  setEventSource(es: EventSource | null): void
  destroyEventSource(): void  // 显式销毁
}
```

### 5.5 uiStore

**状态字段**：
```typescript
interface UIStore {
  // 面板状态
  sidebarCollapsed: boolean
  artifactPanelVisible: boolean
  artifactPanelWidth: number  // 可拖拽调整
  
  // 视图模式
  artifactViewMode: 'raw' | 'rendered'
  diffModeEnabled: boolean
  
  // Actions
  toggleSidebar(): void
  toggleArtifactPanel(): void
  setArtifactPanelWidth(width: number): void
  setArtifactViewMode(mode: 'raw' | 'rendered'): void
}
```

---

## 6. 数据流设计

### 6.1 发送消息流程

```
用户输入 → ChatInput
              │
              ▼
        streamStore.reset()
        conversationStore.addOptimisticMessage() [v1.1: status='sending']
              │
              ▼
        POST /api/v1/chat
              │
              ├─── 失败 ───→ conversationStore.failMessage() [v1.1: status='error']
              │                     │
              │                     ▼
              │              显示错误状态 + 重试按钮
              │
              ▼ 成功
        conversationStore.confirmMessage() [v1.1: status='sent']
        获取 thread_id, stream_url
              │
              ▼
        streamStore.startStream(threadId)
        [v1.1] streamStore.destroyEventSource() // 确保清理旧连接
        EventSource 连接 stream_url
        [v1.1] streamStore.setEventSource(es)
              │
              ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  [v1.2 更新] SSE 事件处理循环（对齐后端 StreamEventType）       │
        │                                                              │
        │  metadata         → 更新 conversation state                 │
        │  agent_start      → streamStore.setCurrentAgent(agent)       │
        │  llm_chunk        → streamStore.setContent(data.content)     │
        │                     streamStore.setReasoning(data.reasoning) │
        │                     （注意：后端发送的是累积值，直接替换）      │
        │  llm_complete     → （可忽略，单次 LLM 调用完成标记）          │
        │  agent_complete   → （agent 本轮完成，可用于 UI 分隔标记）     │
        │  tool_start       → streamStore.addToolStart({running})      │
        │  tool_complete    → streamStore.updateToolComplete(result)    │
        │                     若 artifact 工具 → markPendingUpdate     │
        │  permission_request → streamStore.requestPermission(data)    │
        │  complete         → 检查 data.interrupted:                   │
        │                     true → 中断处理（权限确认）               │
        │                     false → 正常完成                         │
        │  error            → 错误处理                                 │
        └──────────────────────────────────────────────────────────────┘
              │
              ▼
        [v1.1] 收到终止事件后，服务端关闭连接
        streamStore.endStream()
        streamStore.destroyEventSource()
        conversationStore.updateMessage()
        artifactStore.fetchArtifacts() (如有更新)
```

### 6.2 加载历史对话流程

```
路由跳转 /chat/[id]
              │
              ▼
        conversationStore.fetchConversation(id)
              │
              ▼
        API 返回对话详情 (messages 数组)
              │
              ▼
        构建消息树，确定当前分支路径
              │
              ▼
        渲染 MessageList
        （只显示 response，不显示中间过程）
              │
              ▼
        artifactStore.fetchArtifacts(sessionId)
              │
              ▼
        渲染 ArtifactPanel
```

### 6.3 权限确认流程

**[v1.2 更新] SSE 连接生命周期管理（对齐后端事件协议）**：

后端权限中断的事件序列：`permission_request` → `interrupt()` → `complete(interrupted=true)`。
注意：不存在独立的 `interrupt` 事件类型，中断信息通过 `complete` 事件的 `data.interrupted=true` 传递。

```
收到 permission_request 事件
              │
              ▼
        streamStore.requestPermission(data)
        （此时 SSE 连接仍然存在，等待后续 complete 事件）
              │
              ▼
收到 complete 事件（data.interrupted = true）
              │
              ▼
        [v1.2] 服务端关闭 SSE 连接
        streamStore.destroyEventSource()  // 显式销毁
              │
              ▼
        显示 PermissionDialog
        （此时无 SSE 连接，避免长时间挂起）
        （dialog 中展示 data.interrupt_data 中的工具信息）
              │
              ▼
        用户点击 确认/拒绝
              │
              ▼
        POST /api/v1/chat/{id}/resume
        （body: { thread_id, conversation_id, message_id, approved: bool }）
              │
              ▼
        获取新的 stream_url
              │
              ▼
        确保旧 EventSource 已销毁
        创建新 EventSource 连接
        streamStore.setEventSource(newEs)
              │
              ▼
        继续流式处理（新的 SSE 流中会包含 permission_result → tool_start → ...）
```

**[v1.2 更新] 关键代码示例**：

```typescript
// hooks/useSSE.ts
// [v1.2] 重写事件分发，对齐后端 StreamEventType

function useSSE(options: SSEOptions) {
  const { setEventSource, destroyEventSource } = useStreamStore();

  useEffect(() => {
    if (!options.url) return;

    // 创建新连接前，确保销毁旧连接
    destroyEventSource();

    const es = new EventSource(options.url);
    setEventSource(es);

    es.onmessage = (event) => {
      const data = JSON.parse(event.data);

      // [v1.2] 使用 data.type（后端 StreamEventType.value）分发事件
      switch (data.type) {
        // === Controller 层 ===
        case 'metadata':
          options.onEvent?.(data);
          break;

        // === Agent 层 ===
        case 'agent_start':
        case 'llm_chunk':
        case 'llm_complete':
        case 'agent_complete':
          options.onEvent?.(data);
          break;

        // === Graph 层 ===
        case 'tool_start':
        case 'tool_complete':
        case 'permission_request':
        case 'permission_result':
          options.onEvent?.(data);
          break;

        // === 终止事件 ===
        case 'complete':
        case 'error':
          options.onEvent?.(data);
          destroyEventSource();  // 终止事件后关闭连接
          break;
      }
    };

    es.onerror = () => {
      destroyEventSource();
    };

    return () => {
      destroyEventSource();
    };
  }, [options.url]);
}

// stores/streamStore.ts

const useStreamStore = create<StreamStore>((set, get) => ({
  eventSourceRef: null,
  streamContent: '',
  reasoningContent: '',
  currentAgent: null,
  toolCalls: [],

  setEventSource: (es) => set({ eventSourceRef: es }),

  destroyEventSource: () => {
    const { eventSourceRef } = get();
    if (eventSourceRef) {
      eventSourceRef.close();
      set({ eventSourceRef: null });
    }
  },

  // [v1.2] 直接替换（后端发送累积值）
  setContent: (content) => set({ streamContent: content }),
  setReasoning: (reasoning) => set({ reasoningContent: reasoning }),

  setCurrentAgent: (agent) => set({ currentAgent: agent }),

  // [v1.2] tool_start → 添加 running 状态
  addToolStart: (info) => set((state) => ({
    toolCalls: [...state.toolCalls, info]
  })),

  // [v1.2] tool_complete → 更新最后一个匹配的 toolCall
  updateToolComplete: (toolName, result) => set((state) => ({
    toolCalls: state.toolCalls.map((tc) =>
      tc.toolName === toolName && tc.status === 'running'
        ? { ...tc, ...result }
        : tc
    )
  })),

  // ... 其他 actions
}));
```

---

## 7. 关键交互细节

### 7.1 流式消息渲染

**设计要点**：
- 使用 CSS 动画实现光标闪烁效果
- 思考内容 (`reasoning_content`) 默认折叠，点击展开
- 工具调用显示为内联指示器（图标 + 名称 + 状态）
- Agent 切换时显示分隔标记

**[v1.2 新增] 折叠逻辑规范**：

| UI 元素 | 默认状态 | 可否折叠 | 说明 |
|---------|---------|---------|------|
| Agent output (content) | 展开 | 不可折叠 | 核心输出，始终可见 |
| Reasoning/Thinking | 折叠 | 可折叠展开 | 默认折叠，点击展开查看思考过程 |
| Tool calls (组) | 折叠 | 可折叠展开 | 折叠时显示简要状态，展开查看参数和返回结果 |
| Agent 切换标记 | 折叠 | 可折叠展开 | 只显示 agent 名称标签，展开查看详情 |
| Permission dialog | 展开 | 不可折叠 | 需要用户操作，始终可见 |

**[v1.2 新增] Thinking vs Output 实时区分**：

后端 `llm_chunk` 事件中 `data.content` 和 `data.reasoning_content` 是两个独立的累积字段。前端通过对比前后状态来确定当前增量属于哪个区域：

```typescript
// 在 onEvent 处理 llm_chunk 时
const prevContent = streamStore.streamContent;
const prevReasoning = streamStore.reasoningContent;
const newContent = data.data?.content || '';
const newReasoning = data.data?.reasoning_content || '';

// 判断哪个字段发生了变化
if (newReasoning !== prevReasoning) {
  // reasoning 增长 → 当前正在 thinking
  // UI: 更新 ReasoningBlock（如果已折叠，可显示 "thinking..." 指示器）
}
if (newContent !== prevContent) {
  // content 增长 → 当前正在 output
  // UI: 更新 StreamingContent（打字机效果）
}

// 替换累积值（非 append）
streamStore.setContent(newContent);
streamStore.setReasoning(newReasoning);
```

**渲染策略**：
```
if (isStreaming) {
  // 实时渲染模式
  显示 StreamingMessage 组件
  - 显示 Agent 名称（来自 agent_start 事件）
  - 显示思考过程（可折叠，来自 llm_chunk.reasoning_content）
  - 显示工具调用状态（来自 tool_start / tool_complete 事件）
  - 显示实时输出（打字机效果，来自 llm_chunk.content）
} else {
  // 历史模式
  显示 AssistantMessage 组件
  - 只显示最终 response
  - 不显示中间过程
}
```

**[v1.1 新增] 消息状态渲染**：
```typescript
// 根据消息状态渲染不同 UI
function MessageItem({ message }: { message: Message }) {
  if (message.status === 'sending') {
    return (
      <div className="opacity-70">
        {/* 消息内容 + 发送中指示器 */}
        <Spinner size="sm" />
      </div>
    );
  }
  
  if (message.status === 'error') {
    return (
      <div className="border-red-500">
        {/* 消息内容 + 错误提示 */}
        <span className="text-red-500">{message.error}</span>
        <Button onClick={() => retryMessage(message.id)}>重试</Button>
        <Button variant="ghost" onClick={() => removeMessage(message.id)}>删除</Button>
      </div>
    );
  }
  
  // status === 'sent'
  return <NormalMessage message={message} />;
}
```

### 7.2 分支对话交互

**UI 设计**：
- 有分支的消息显示分支图标
- 点击分支图标展开分支选择器
- 选择分支后切换显示该分支的消息

**交互流程**：
1. 用户点击历史消息的「编辑」按钮
2. 显示编辑输入框
3. 用户提交新消息
4. 调用 API，`parent_message_id` 设为被编辑消息的 ID
5. 创建新分支，自动切换到新分支

### 7.3 Artifact 版本对比

**Monaco Diff View 集成**：
- 使用 `monaco-editor` 的 `DiffEditor` 组件
- 左侧显示旧版本，右侧显示新版本
- 高亮差异区域
- 版本选择器控制对比的两个版本

**交互流程**：
1. 用户点击「对比」按钮
2. 进入 Diff 模式
3. 默认对比当前版本和上一版本
4. 可通过选择器调整对比的版本
5. 点击「退出对比」返回单版本视图

### 7.4 Artifact 更新策略

**[v1.2 更新] 分阶段渲染策略**：

| 阶段 | 触发时机 | 行为 | 说明 |
|------|----------|------|------|
| **Phase 1 (MVP)** | 监听 `tool_complete` 事件 | 工具执行完成后，标记 artifact 有更新；流式结束后统一刷新 | 简单可靠，避免频繁请求 |
| **Phase 1+ (增强)** | `tool_complete` 事件后 | 允许用户点击 tab 主动触发单个 artifact 刷新 | 不必等全部完成 |
| **Phase 2 (进阶)** | 监听流中的内容块 | 实时更新 Artifact 内容，实现打字机效果 | 需要后端支持增量推送 |

**Phase 1 实现（当前目标）**：

> [v1.2 修正] 后端事件类型为 `tool_complete`（非 `tool_result`）。
> `tool_complete` 事件的 data 结构为：
> ```json
> {
>   "type": "tool_complete",
>   "agent": "lead_agent",
>   "tool": "create_artifact",
>   "timestamp": "...",
>   "data": {
>     "success": true,
>     "duration_ms": 123,
>     "error": null,
>     "params": { "id": "my_artifact", "title": "..." },
>     "result_data": { "message": "Created artifact 'my_artifact'" }
>   }
> }
> ```
> 注意：`params` 包含工具调用参数（`params.id` 即 artifact_id），`result_data` 包含工具返回的业务数据。

```typescript
// SSE 事件处理
function handleStreamEvent(event: StreamEvent) {
  if (event.type === 'tool_complete') {
    const toolName = event.tool;
    const { success, params, result_data } = event.data;

    // 检查是否是 Artifact 操作工具（通过 params.id 获取 artifact_id）
    const ARTIFACT_TOOLS = ['create_artifact', 'update_artifact', 'rewrite_artifact'];
    if (ARTIFACT_TOOLS.includes(toolName) && success && params?.id) {
      // 标记该 artifact 有更新（Tab 显示小圆点）
      artifactStore.markPendingUpdate(params.id);
    }
  }

  if (event.type === 'complete') {
    // 流式结束后，刷新有更新的 artifacts
    if (artifactStore.pendingUpdates.size > 0) {
      artifactStore.fetchArtifacts(sessionId);
      artifactStore.clearPendingUpdates();
    }
  }
}
```

**Phase 2 预留设计**：
```typescript
// 未来：后端推送 artifact 内容块
interface ArtifactContentChunk {
  artifact_id: string;
  content_delta: string;  // 增量内容
  version: number;
}

// 前端实时拼接
function handleArtifactChunk(chunk: ArtifactContentChunk) {
  artifactStore.appendContent(chunk.artifact_id, chunk.content_delta);
  // Monaco Editor 自动滚动到最新位置
}
```

---

## 8. 文件结构规划

```
frontend/
├── package.json
├── next.config.js
├── tailwind.config.js
├── tsconfig.json
│
├── public/
│   └── favicon.ico
│
├── src/
│   ├── app/                          # Next.js App Router
│   │   ├── layout.tsx                # 根布局
│   │   ├── page.tsx                  # 首页
│   │   ├── globals.css               # 全局样式
│   │   │
│   │   ├── chat/
│   │   │   ├── layout.tsx            # Chat 布局（双面板）
│   │   │   ├── page.tsx              # 新对话
│   │   │   └── [id]/
│   │   │       └── page.tsx          # 对话详情
│   │   │
│   │   └── (auth)/                   # 预留：认证
│   │       ├── login/page.tsx
│   │       └── register/page.tsx
│   │
│   ├── components/                   # 组件
│   │   ├── ui/                       # shadcn/ui 组件
│   │   │   ├── button.tsx
│   │   │   ├── dialog.tsx
│   │   │   ├── dropdown-menu.tsx
│   │   │   └── ...
│   │   │
│   │   ├── layout/                   # 布局组件
│   │   │   ├── Header.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   └── ResizablePanel.tsx
│   │   │
│   │   ├── chat/                     # 对话相关组件
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── MessageItem.tsx
│   │   │   ├── UserMessage.tsx
│   │   │   ├── AssistantMessage.tsx
│   │   │   ├── StreamingMessage.tsx
│   │   │   ├── ReasoningBlock.tsx
│   │   │   ├── ToolCallIndicator.tsx
│   │   │   ├── ChatInput.tsx
│   │   │   ├── PermissionDialog.tsx
│   │   │   └── MessageStatusIndicator.tsx  # [v1.1 新增]
│   │   │
│   │   ├── artifact/                 # Artifact 相关组件
│   │   │   ├── ArtifactPanel.tsx
│   │   │   ├── ArtifactTabs.tsx
│   │   │   ├── ArtifactViewer.tsx
│   │   │   ├── MonacoViewer.tsx
│   │   │   ├── DiffViewer.tsx
│   │   │   ├── MarkdownRenderer.tsx
│   │   │   └── VersionSelector.tsx
│   │   │
│   │   └── conversation/             # 对话列表组件
│   │       ├── ConversationList.tsx
│   │       └── ConversationItem.tsx
│   │
│   ├── stores/                       # Zustand stores
│   │   ├── conversationStore.ts
│   │   ├── artifactStore.ts
│   │   ├── streamStore.ts
│   │   └── uiStore.ts
│   │
│   ├── hooks/                        # 自定义 hooks
│   │   ├── useSSE.ts                 # SSE 连接 hook [v1.1 增强]
│   │   ├── useConversation.ts        # 对话相关 hook
│   │   └── useArtifact.ts            # Artifact 相关 hook
│   │
│   ├── lib/                          # 工具库
│   │   ├── api.ts                    # API 客户端
│   │   ├── sse.ts                    # SSE 客户端 [v1.1 增强]
│   │   └── utils.ts                  # 通用工具函数
│   │
│   └── types/                        # TypeScript 类型定义
│       ├── conversation.ts           # [v1.1 更新: 增加 MessageStatus]
│       ├── artifact.ts
│       ├── stream.ts
│       └── api.ts
│
└── README.md
```

---

## 9. 核心 Hooks 设计

### 9.1 useSSE

**[v1.2 重写] 职责**：
- 管理 EventSource 连接
- 解析 SSE 事件（**对齐后端 11 种 StreamEventType**）
- **显式管理连接生命周期**
- 统一的事件回调接口

**接口**：
```typescript
// [v1.2] SSE 事件（对应后端 StreamEventType）
interface SSEEvent {
  type: 'metadata' | 'agent_start' | 'llm_chunk' | 'llm_complete' | 'agent_complete'
        | 'tool_start' | 'tool_complete' | 'permission_request' | 'permission_result'
        | 'complete' | 'error';
  agent?: string;
  tool?: string;
  timestamp: string;
  data?: any;
}

function useSSE(options: {
  url: string | null
  onEvent: (event: SSEEvent) => void  // [v1.2] 统一事件回调，替代分散的 onMetadata/onStream/...
  onConnectionError?: (error: Event) => void
}): {
  isConnected: boolean
  disconnect: () => void
}
```

**[v1.2] 实现要点**：
```typescript
function useSSE(options: SSEOptions) {
  const [isConnected, setIsConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
      setIsConnected(false);
    }
  }, []);

  useEffect(() => {
    if (!options.url) {
      disconnect();
      return;
    }

    disconnect();  // 创建新连接前先断开旧连接

    const es = new EventSource(options.url);
    eventSourceRef.current = es;

    es.onopen = () => setIsConnected(true);

    es.onmessage = (event) => {
      const data: SSEEvent = JSON.parse(event.data);

      // [v1.2] 使用 data.type 分发（后端 StreamEventType.value）
      // 所有事件统一回调，由调用方在 onEvent 中处理各种类型
      options.onEvent(data);

      // 终止事件后关闭连接
      if (data.type === 'complete' || data.type === 'error') {
        disconnect();
      }
    };

    es.onerror = (error) => {
      options.onConnectionError?.(error);
      disconnect();
    };

    return () => disconnect();
  }, [options.url]);

  return { isConnected, disconnect };
}
```

**[v1.2] 调用方事件处理示例**：
```typescript
// 在 ChatPanel 或 useConversation 中使用
useSSE({
  url: streamUrl,
  onEvent: (event) => {
    switch (event.type) {
      // === Agent 生命周期 ===
      case 'agent_start':
        streamStore.setCurrentAgent(event.agent!);
        break;

      case 'llm_chunk':
        // 注意：data.content 和 data.reasoning_content 是累积值
        streamStore.setContent(event.data?.content || '');
        streamStore.setReasoning(event.data?.reasoning_content || '');
        break;

      case 'llm_complete':
        // 单次 LLM 调用完成，可选：更新 token 统计
        break;

      case 'agent_complete':
        // agent 本轮完成，可用于 UI 分隔标记
        break;

      // === 工具调用 ===
      case 'tool_start':
        streamStore.addToolStart({
          toolName: event.tool!,
          agent: event.agent!,
          status: 'running',
          params: event.data?.params,
        });
        break;

      case 'tool_complete':
        streamStore.updateToolComplete(event.tool!, {
          status: event.data?.success ? 'success' : 'failed',
          error: event.data?.error,
          durationMs: event.data?.duration_ms,
          resultData: event.data?.result_data,
        });
        // 检查是否是 Artifact 操作（通过 params.id 获取 artifact_id）
        const ARTIFACT_TOOLS = ['create_artifact', 'update_artifact', 'rewrite_artifact'];
        if (ARTIFACT_TOOLS.includes(event.tool!) && event.data?.success && event.data?.params?.id) {
          artifactStore.markPendingUpdate(event.data.params.id);
        }
        break;

      // === 权限 ===
      case 'permission_request':
        streamStore.requestPermission(event.data);
        break;

      case 'permission_result':
        // 恢复后收到，可用于 UI 更新
        break;

      // === 会话 ===
      case 'metadata':
        conversationStore.updateMetadata(event.data);
        break;

      case 'complete':
        if (event.data?.interrupted) {
          // 中断：显示 PermissionDialog（连接已在 useSSE 中关闭）
        } else {
          // 正常完成
          streamStore.endStream();
          conversationStore.updateMessage(event.data);
          if (artifactStore.pendingUpdates.size > 0) {
            artifactStore.fetchArtifacts(sessionId);
            artifactStore.clearPendingUpdates();
          }
        }
        break;

      case 'error':
        streamStore.endStream();
        // 显示错误提示
        break;
    }
  },
});
```

### 9.2 useConversation

**职责**：
- 封装对话相关的状态和操作
- 提供发送消息、切换分支等方法

**接口**：
```typescript
function useConversation(conversationId?: string): {
  // 状态
  conversation: ConversationDetail | null
  messages: Message[]
  isLoading: boolean
  error: Error | null
  
  // 流式状态
  isStreaming: boolean
  streamContent: string
  
  // 操作
  sendMessage: (content: string, parentId?: string) => Promise<void>
  switchBranch: (messageId: string) => void
  deleteConversation: () => Promise<void>
  
  // [v1.1 新增] 消息状态操作
  retryMessage: (messageId: string) => Promise<void>
  removeFailedMessage: (messageId: string) => void
}
```

### 9.3 useArtifact

**职责**：
- 封装 Artifact 相关的状态和操作
- 提供版本切换、Diff 对比等方法

**接口**：
```typescript
function useArtifact(sessionId: string): {
  // 状态
  artifacts: ArtifactSummary[]
  currentArtifact: ArtifactDetail | null
  currentVersion: number
  isLoading: boolean
  
  // [v1.1 新增] 更新指示
  hasPendingUpdates: boolean
  
  // Diff 状态
  diffMode: boolean
  compareVersion: number | null
  
  // 操作
  selectArtifact: (artifactId: string) => void
  selectVersion: (version: number) => Promise<void>
  enableDiff: (baseVersion: number) => void
  disableDiff: () => void
  refreshArtifacts: () => Promise<void>  // [v1.1 新增]
}
```

---

## 10. 实施步骤

### Phase 1: 项目搭建（预计 1-2 天）

1. **初始化项目**
   - 创建 Next.js 14 项目
   - 配置 Tailwind CSS
   - 安装 shadcn/ui

2. **搭建基础布局**
   - Header 组件
   - Sidebar 组件
   - 双面板布局

3. **配置 Zustand**
   - 创建基础 stores
   - **[v1.1] 实现消息状态管理（status 字段）**
   - 配置持久化（localStorage）

### Phase 2: 对话功能（预计 3-4 天）

1. **对话列表**
   - ConversationList 组件
   - 分页加载
   - 删除功能

2. **消息展示**
   - MessageList 组件
   - UserMessage / AssistantMessage 组件
   - **[v1.1] MessageStatusIndicator 组件（发送中/错误状态）**
   - 消息树渲染逻辑

3. **消息发送**
   - ChatInput 组件
   - API 集成
   - **[v1.1] 乐观更新 + 安全回滚机制**

4. **SSE 流式**
   - useSSE hook
   - **[v1.1] EventSource 生命周期管理**
   - StreamingMessage 组件
   - 打字机效果

### Phase 3: Artifact 功能（预计 2-3 天）

1. **Artifact 展示**
   - ArtifactPanel 组件
   - ArtifactTabs 组件
   - 版本选择器
   - **[v1.1] 更新指示器（小圆点）**

2. **Monaco Editor 集成**
   - MonacoViewer 组件
   - DiffViewer 组件

3. **Markdown 渲染**
   - MarkdownRenderer 组件
   - 代码高亮

### Phase 4: 高级功能（预计 2-3 天）

1. **分支对话**
   - 分支 UI 指示器
   - 分支切换逻辑
   - 从历史消息创建分支

2. **权限确认**
   - PermissionDialog 组件
   - **[v1.1] SSE 连接生命周期管理（销毁旧实例→创建新实例）**
   - 恢复执行流程

3. **工具调用展示**
   - ToolCallIndicator 组件
   - ReasoningBlock 组件

### Phase 5: 优化和测试（预计 2-3 天）

1. **性能优化**
   - 虚拟滚动（长对话）
   - 代码分割
   - 图片懒加载（预留）

2. **响应式适配**
   - 移动端布局
   - 面板折叠逻辑

3. **测试**
   - 组件单元测试
   - **[v1.1] SSE 连接生命周期测试**
   - **[v1.1] 乐观更新回滚测试**
   - E2E 测试（Playwright）

---

## 11. 后续扩展预留

### 11.1 用户认证（Phase 2）

**预留位置**：
- `app/(auth)/` 路由组
- `stores/authStore.ts`
- `middleware.ts` 路由保护

**实现要点**：
- JWT Token 存储（httpOnly cookie 或 localStorage）
- 登录/注册页面
- 路由中间件保护

### 11.2 更多功能预留

**文件上传**：
- ChatInput 添加 AttachButton
- 支持图片、文档上传
- 预览组件

**Artifact 导出**：
- 导出为本地文件
- 复制到剪贴板

**主题切换**：
- 深色/浅色主题
- 使用 CSS 变量 + Tailwind 实现

---

## 12. 依赖清单

```json
{
  "dependencies": {
    "next": "^14.1.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    
    "zustand": "^4.5.0",
    
    "@radix-ui/react-dialog": "^1.0.5",
    "@radix-ui/react-dropdown-menu": "^2.0.6",
    "@radix-ui/react-tabs": "^1.0.4",
    
    "@monaco-editor/react": "^4.6.0",
    "react-markdown": "^9.0.1",
    "remark-gfm": "^4.0.0",
    "react-syntax-highlighter": "^15.5.0",
    
    "tailwindcss": "^3.4.1",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.0",
    "tailwind-merge": "^2.2.1",
    
    "lucide-react": "^0.330.0"
  },
  "devDependencies": {
    "typescript": "^5.3.3",
    "@types/node": "^20.11.0",
    "@types/react": "^18.2.48",
    "eslint": "^8.56.0",
    "eslint-config-next": "^14.1.0",
    
    "playwright": "^1.41.0"
  }
}
```

---

## 13. 开发环境配置

```typescript
// next.config.js
module.exports = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*', // 开发时代理到后端
      },
    ]
  },
}
```

```env
# .env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## 附录 A：v1.0 → v1.1 变更摘要

| Section | 变更内容 | 类型 |
|---------|----------|------|
| 5.2 conversationStore | 新增 `MessageStatus` 类型和 `status` 字段 | 新增 |
| 5.2 conversationStore | 新增乐观更新相关 Actions | 新增 |
| 5.3 artifactStore | 新增 `pendingUpdates` 状态 | 新增 |
| 5.4 streamStore | 新增 `eventSourceRef` 和生命周期管理方法 | 新增 |
| 6.1 发送消息流程 | 更新流程图，加入状态管理和连接生命周期 | 更新 |
| 6.3 权限确认流程 | 重写流程，明确 SSE 连接销毁和重建 | 更新 |
| 7.1 流式消息渲染 | 新增消息状态渲染逻辑 | 新增 |
| 7.4 Artifact 更新策略 | 明确分阶段渲染策略（Phase 1/2） | 更新 |
| 8. 文件结构 | 新增 `MessageStatusIndicator.tsx` | 更新 |
| 9.1 useSSE | 增强接口，新增 `onInterrupt` 和 `disconnect` | 更新 |
| 9.1 useSSE | 添加完整实现示例 | 新增 |
| 10. 实施步骤 | 各阶段加入 v1.1 相关任务 | 更新 |

## 附录 B：v1.1 → v1.2 变更摘要

> **核心改动**：对齐后端 `StreamEventType` 事件协议，修正前端设计文档中所有与后端实现不一致的地方。

| Section | 变更内容 | 类型 | 优先级 |
|---------|----------|------|--------|
| 5.4 streamStore | 新增后端 StreamEventType 事件对照表 | 新增 | P0 |
| 5.4 streamStore | 说明 `llm_chunk` 是累积值非 delta | 新增 | P0 |
| 5.4 streamStore | `appendContent/appendReasoning` → `setContent/setReasoning` | 修正 | P0 |
| 5.4 streamStore | 新增 `ToolCallInfo` 类型定义 | 新增 | P1 |
| 5.4 streamStore | 新增 `setCurrentAgent/addToolStart/updateToolComplete` actions | 新增 | P1 |
| 6.1 发送消息流程 | SSE 事件处理循环对齐全部 11 种后端事件类型 | 修正 | P0 |
| 6.3 权限确认流程 | 修正事件序列：`permission_request` → `complete(interrupted=true)` | 修正 | P1 |
| 6.3 代码示例 | 重写 useSSE 使用 `data.type` 分发，重写 streamStore actions | 修正 | P0 |
| 7.1 流式消息渲染 | 新增折叠逻辑规范表 | 新增 | P2 |
| 7.1 流式消息渲染 | 新增 Thinking vs Output 实时区分机制 | 新增 | P2 |
| 7.4 Artifact 更新策略 | `tool_result` → `tool_complete`，修正事件 data 结构 | 修正 | P0 |
| 7.4 Artifact 更新策略 | 使用 `event.tool` 和 `event.data.result_data` 匹配实际后端 | 修正 | P0 |
| 9.1 useSSE | 重写接口：`onMetadata/onStream/...` → 统一 `onEvent(SSEEvent)` | 修正 | P0 |
| 9.1 useSSE | `data.event_type` → `data.type`（匹配后端字段名） | 修正 | P0 |
| 9.1 useSSE | 新增调用方事件处理完整示例 | 新增 | P1 |
