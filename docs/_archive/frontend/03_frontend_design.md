# ArtifactFlow 前端实现方案

> 版本: v1.1 | 依赖: API 层完成
> 
> **v1.1 变更**：整合架构优化建议，增强状态管理、明确渲染策略、规范 SSE 生命周期

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

**状态字段**：
```typescript
interface StreamStore {
  // 当前流式状态
  isStreaming: boolean
  currentThreadId: string | null
  
  // 流式内容累积
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
  appendContent(content: string): void
  appendReasoning(reasoning: string): void
  addToolCall(toolCall: ToolCallInfo): void
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
        ┌─────────────────────────────────────┐
        │         SSE 事件处理循环             │
        │                                     │
        │  metadata → 更新 conversation state │
        │  llm_chunk → streamStore.append*   │
        │  tool_* → streamStore.addToolCall  │
        │  permission → streamStore.request  │
        │  complete → 完成处理               │
        │  error → 错误处理                  │
        └─────────────────────────────────────┘
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

**[v1.1 重要改进] SSE 连接生命周期管理**：

```
收到 permission_required 事件
              │
              ▼
        [v1.1] 服务端主动关闭 SSE 连接
              │
              ▼
        streamStore.requestPermission(data)
        streamStore.destroyEventSource()  // [v1.1] 显式销毁旧实例
              │
              ▼
        显示 PermissionDialog
        （此时无 SSE 连接，避免长时间挂起）
              │
              ▼
        用户点击 确认/拒绝
              │
              ▼
        POST /api/v1/chat/{id}/resume
              │
              ▼
        获取新的 stream_url
              │
              ▼
        [v1.1] 确保旧 EventSource 已销毁
        创建新 EventSource 连接
        streamStore.setEventSource(newEs)
              │
              ▼
        继续流式处理
```

**[v1.1 关键代码示例]**：

```typescript
// hooks/useSSE.ts

function useSSE(options: SSEOptions) {
  const { setEventSource, destroyEventSource } = useStreamStore();
  
  useEffect(() => {
    if (!options.url) return;
    
    // [v1.1] 创建新连接前，确保销毁旧连接
    destroyEventSource();
    
    const es = new EventSource(options.url);
    setEventSource(es);
    
    es.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      switch (data.event_type) {
        case 'complete':
        case 'interrupt':
        case 'error':
          // [v1.1] 终止事件：服务端会关闭连接，前端也显式清理
          destroyEventSource();
          break;
        // ... 其他事件处理
      }
    };
    
    es.onerror = () => {
      destroyEventSource();
    };
    
    // [v1.1] 组件卸载时清理
    return () => {
      destroyEventSource();
    };
  }, [options.url]);
}

// stores/streamStore.ts

const useStreamStore = create<StreamStore>((set, get) => ({
  eventSourceRef: null,
  
  setEventSource: (es) => set({ eventSourceRef: es }),
  
  destroyEventSource: () => {
    const { eventSourceRef } = get();
    if (eventSourceRef) {
      eventSourceRef.close();  // [v1.1] 显式关闭
      set({ eventSourceRef: null });
    }
  },
  
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

**渲染策略**：
```
if (isStreaming) {
  // 实时渲染模式
  显示 StreamingMessage 组件
  - 显示 Agent 名称
  - 显示思考过程（可折叠）
  - 显示工具调用状态
  - 显示实时输出（打字机效果）
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

**[v1.1 更新] 分阶段渲染策略**：

| 阶段 | 触发时机 | 行为 | 说明 |
|------|----------|------|------|
| **Phase 1 (MVP)** | 监听 `tool_result` 事件 | 工具执行完成后，标记 artifact 有更新；流式结束后统一刷新 | 简单可靠，避免频繁请求 |
| **Phase 2 (进阶)** | 监听流中的内容块 | 实时更新 Artifact 内容，实现打字机效果 | 需要后端支持增量推送 |

**Phase 1 实现（当前目标）**：
```typescript
// SSE 事件处理
function handleStreamEvent(event: StreamEvent) {
  if (event.type === 'tool_result') {
    const { tool_name, result } = event.data;
    
    // 检查是否是 Artifact 操作工具
    if (['create_artifact', 'update_artifact', 'rewrite_artifact'].includes(tool_name)) {
      if (result.success) {
        // 标记该 artifact 有更新（Tab 显示小圆点）
        artifactStore.markPendingUpdate(result.data.artifact_id);
      }
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

**[v1.1 增强] 职责**：
- 管理 EventSource 连接
- 解析 SSE 事件
- **显式管理连接生命周期**
- 处理重连逻辑

**接口**：
```typescript
function useSSE(options: {
  url: string | null
  onMetadata?: (data: any) => void
  onStream?: (data: any) => void
  onComplete?: (data: any) => void
  onInterrupt?: (data: any) => void  // [v1.1 新增]
  onError?: (error: any) => void
}): {
  isConnected: boolean
  error: Error | null
  disconnect: () => void  // [v1.1] 显式断开方法
}
```

**[v1.1] 实现要点**：
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
      disconnect();  // URL 变为 null 时断开
      return;
    }
    
    // [v1.1] 创建新连接前先断开旧连接
    disconnect();
    
    const es = new EventSource(options.url);
    eventSourceRef.current = es;
    
    es.onopen = () => setIsConnected(true);
    
    es.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      // 分发事件
      switch (data.event_type) {
        case 'metadata':
          options.onMetadata?.(data.data);
          break;
        case 'stream':
          options.onStream?.(data.data);
          break;
        case 'complete':
          options.onComplete?.(data.data);
          disconnect();  // [v1.1] 完成后断开
          break;
        case 'interrupt':
          options.onInterrupt?.(data.data);
          disconnect();  // [v1.1] 中断后断开
          break;
        case 'error':
          options.onError?.(data.data);
          disconnect();  // [v1.1] 错误后断开
          break;
      }
    };
    
    es.onerror = (error) => {
      options.onError?.(error);
      disconnect();
    };
    
    return () => disconnect();  // [v1.1] 组件卸载时清理
  }, [options.url]);
  
  return { isConnected, error: null, disconnect };
}
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

## 附录：v1.0 → v1.1 变更摘要

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
