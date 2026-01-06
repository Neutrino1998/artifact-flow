# ArtifactFlow 前端实现方案

> 版本: v1.0 | 依赖: API 层完成

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
  
  // Actions
  fetchArtifacts(sessionId: string): Promise<void>
  fetchArtifact(sessionId: string, artifactId: string): Promise<void>
  fetchVersion(sessionId: string, artifactId: string, version: number): Promise<void>
  selectArtifact(artifactId: string): void
  selectVersion(version: number): void
  enableDiffMode(baseVersion: number): void
  disableDiffMode(): void
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
  
  // Actions
  startStream(threadId: string): void
  appendContent(content: string): void
  appendReasoning(reasoning: string): void
  addToolCall(toolCall: ToolCallInfo): void
  requestPermission(request: PermissionRequest): void
  resolvePermission(approved: boolean): Promise<void>
  endStream(): void
  reset(): void
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
        conversationStore.createMessage() (乐观更新)
              │
              ▼
        POST /api/v1/chat
              │
              ▼
        获取 thread_id, stream_url
              │
              ▼
        streamStore.startStream(threadId)
        EventSource 连接 stream_url
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
        streamStore.endStream()
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

```
收到 permission_required 事件
              │
              ▼
        streamStore.requestPermission(data)
              │
              ▼
        显示 PermissionDialog
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
        重新订阅 SSE
              │
              ▼
        继续流式处理
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

### 7.4 Artifact 自动更新

**设计**：
- 在流式过程中监听 `tool_result` 事件
- 如果工具是 `create_artifact`/`update_artifact`/`rewrite_artifact`
- 标记对应 artifact 为「有更新」
- 流式结束后刷新 artifact 列表

**UI 反馈**：
- Tab 上显示更新指示器（小圆点）
- 自动切换到最新更新的 artifact

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
│   │   │   └── PermissionDialog.tsx
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
│   │   ├── useSSE.ts                 # SSE 连接 hook
│   │   ├── useConversation.ts        # 对话相关 hook
│   │   └── useArtifact.ts            # Artifact 相关 hook
│   │
│   ├── lib/                          # 工具库
│   │   ├── api.ts                    # API 客户端
│   │   ├── sse.ts                    # SSE 客户端
│   │   └── utils.ts                  # 通用工具函数
│   │
│   └── types/                        # TypeScript 类型定义
│       ├── conversation.ts
│       ├── artifact.ts
│       ├── stream.ts
│       └── api.ts
│
└── README.md
```

---

## 9. 核心 Hooks 设计

### 9.1 useSSE

**职责**：
- 管理 EventSource 连接
- 解析 SSE 事件
- 处理重连逻辑

**接口**：
```typescript
function useSSE(options: {
  url: string | null
  onMetadata?: (data: any) => void
  onStream?: (data: any) => void
  onComplete?: (data: any) => void
  onError?: (error: any) => void
}): {
  isConnected: boolean
  error: Error | null
  disconnect: () => void
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
  
  // Diff 状态
  diffMode: boolean
  compareVersion: number | null
  
  // 操作
  selectArtifact: (artifactId: string) => void
  selectVersion: (version: number) => Promise<void>
  enableDiff: (baseVersion: number) => void
  disableDiff: () => void
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
   - 配置持久化（localStorage）

### Phase 2: 对话功能（预计 3-4 天）

1. **对话列表**
   - ConversationList 组件
   - 分页加载
   - 删除功能

2. **消息展示**
   - MessageList 组件
   - UserMessage / AssistantMessage 组件
   - 消息树渲染逻辑

3. **消息发送**
   - ChatInput 组件
   - API 集成
   - 乐观更新

4. **SSE 流式**
   - useSSE hook
   - StreamingMessage 组件
   - 打字机效果

### Phase 3: Artifact 功能（预计 2-3 天）

1. **Artifact 展示**
   - ArtifactPanel 组件
   - ArtifactTabs 组件
   - 版本选择器

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
