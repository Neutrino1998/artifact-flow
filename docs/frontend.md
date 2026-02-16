# 前端架构指南

本文档帮助你理解 ArtifactFlow 前端的完整架构，适合刚接触这个项目的开发者阅读。

## 技术栈

| 技术 | 版本 | 用途 |
|------|------|------|
| Next.js | 15 | React 框架（App Router 模式） |
| React | 19 | UI 库 |
| TypeScript | strict mode | 类型安全 |
| Tailwind CSS | 3 | 原子化 CSS |
| Zustand | 5 | 状态管理 |

## 目录结构总览

```
frontend/src/
├── app/                  # Next.js 入口（路由 + 全局样式）
│   └── login/           # 登录页
├── components/           # React 组件
│   ├── layout/          # 页面布局
│   ├── sidebar/         # 左侧栏（对话列表）
│   ├── chat/            # 中间栏（聊天面板）
│   └── artifact/        # 右侧栏（文稿面板）
├── hooks/                # 自定义 Hook
├── lib/                  # 工具函数（API 客户端、SSE、消息树）
├── stores/               # Zustand 状态仓库（含 authStore）
└── types/                # TypeScript 类型定义
```

---

## 核心概念

### 什么是 Hook？

Hook 是 React 的一种模式，用来把**可复用的逻辑**从组件中抽离出来。函数名以 `use` 开头。

比如 `useChat` 封装了「发送消息、刷新对话」等操作，任何需要聊天功能的组件只需调用 `useChat()` 就能拿到这些函数，而不用重复写逻辑。

```tsx
// 使用示例
function MyComponent() {
  const { sendMessage, isNewConversation } = useChat();
  // 现在可以直接调用 sendMessage("hello")
}
```

### 什么是 Store？

Store（状态仓库）是用 Zustand 创建的全局状态容器。它解决的问题是：**多个不相关的组件需要共享同一份数据**。

比如 `streamStore` 保存了当前 SSE 流的状态（是否在流式传输、当前内容、工具调用等），聊天面板和消息列表都需要读取这些数据。

```tsx
// 通过 selector 读取单个字段（推荐，避免不必要的重渲染）
const isStreaming = useStreamStore((s) => s.isStreaming);

// 调用 action 修改状态
const startStream = useStreamStore((s) => s.startStream);
startStream(url, threadId, messageId);
```

> **Selector 模式**：`useStreamStore((s) => s.isStreaming)` 只在 `isStreaming` 变化时触发重渲染。如果写成 `useStreamStore()` 获取整个 store，那么 store 里任何字段变化都会导致重渲染，在高频率流式更新时会造成性能问题。

---

## 页面结构

整个应用只有一个页面 `app/page.tsx`，采用三栏布局：

```
┌──────────┬──────────────────────┬──────────────┐
│          │                      │              │
│ Sidebar  │     Chat Panel       │  Artifact    │
│ 对话列表  │     聊天面板          │  文稿面板     │
│          │                      │              │
│          │                      │              │
└──────────┴──────────────────────┴──────────────┘
```

- **Sidebar**：对话列表、新建对话、主题切换
- **Chat Panel**：消息历史 + 流式响应 + 输入框
- **Artifact Panel**：文稿预览（Markdown）/ 源码 / Diff 对比

Artifact Panel 默认隐藏，当 Agent 创建/更新文稿时自动弹出。

---

## 文件详解

### `app/` — Next.js 入口

| 文件 | 作用 |
|------|------|
| `layout.tsx` | 根布局，设置 HTML metadata、字体、全局样式 |
| `page.tsx` | 首页，组装三栏布局 + 权限确认弹窗（被 `AuthGuard` 包裹） |
| `login/page.tsx` | 登录页，username/password 表单 |
| `globals.css` | 全局 CSS（Tailwind 指令 + 自定义样式） |

### `types/` — 类型定义

| 文件 | 作用 |
|------|------|
| `index.ts` | 手写的业务类型：`ChatRequest`、`ConversationDetail`、`ArtifactDetail`、`LoginRequest`、`UserInfo` 等 |
| `events.ts` | SSE 事件类型：`StreamEventType` 枚举 + 每种事件的数据结构 |
| `api.d.ts` | 从后端 OpenAPI schema 自动生成的类型（`npm run generate-types`） |

`index.ts` 里的类型是前端自己定义的别名，比 `api.d.ts` 更易用。当后端 API 改了字段时，需要重新跑 `generate-types` 再手动更新 `index.ts`。

### `lib/` — 工具函数

#### `api.ts` — API 客户端

封装了所有后端 HTTP 请求，提供类型安全的函数：

```tsx
// 对话相关
listConversations(limit, offset)    // GET  /api/v1/chat
getConversation(convId)             // GET  /api/v1/chat/:id
sendMessage(body)                   // POST /api/v1/chat
deleteConversation(convId)          // DELETE /api/v1/chat/:id
resumeExecution(convId, body)       // POST /api/v1/chat/:id/resume

// 文稿相关
listArtifacts(sessionId)            // GET  /api/v1/artifacts/:sessionId
getArtifact(sessionId, artifactId)  // GET  /api/v1/artifacts/:sessionId/:id
listVersions(sessionId, artifactId) // GET  /api/v1/artifacts/:sessionId/:id/versions
getVersion(sessionId, id, version)  // GET  /api/v1/artifacts/:sessionId/:id/versions/:v
```

内部用一个通用的 `request<T>()` 函数处理 fetch + 错误处理 + JSON 解析。所有请求自动从 `authStore` 读取 token 注入 `Authorization: Bearer <token>` header，401 响应自动触发登出。

新增认证相关函数：
```tsx
login(username, password)              // POST /api/v1/auth/login（无需 token）
```

#### `sse.ts` — SSE 连接

**为什么不用浏览器原生的 `EventSource`？** 因为 `EventSource` 不支持自定义 Header（无法传 `Authorization`），也无法用 `AbortController` 精确取消连接。SSE 连接同样从 `authStore` 读取 token 注入 auth header，401 响应触发登出。

实现方式是 `fetch()` + `ReadableStream` 手动解析 SSE 协议：

```
event: agent_start        ← 事件类型
data: {"agent":"Lead"}    ← JSON 数据
                          ← 空行分隔
event: llm_chunk
data: {"content":"你好"}
```

解析流程：逐行读取 → 遇到 `event:` 记录类型 → 遇到 `data:` 解析 JSON → 回调 `onEvent`。

#### `messageTree.ts` — 消息树

后端返回的消息是**扁平数组**，每条消息带一个 `parent_id` 指向它的父消息。这个文件负责把扁平数组转换成**树结构**，用于支持对话分支。

```
消息A (root)
├── 消息B (parent_id = A)
│   ├── 消息C (parent_id = B)  ← 分支1
│   └── 消息D (parent_id = B)  ← 分支2
└── 消息E (parent_id = A)
```

三个核心函数：

- **`buildMessageTree(messages)`**：扁平数组 → `Map<id, MessageNode>`，每个 node 带 `childNodes`、`siblingIndex`、`siblingCount`
- **`extractBranchPath(nodeMap, activeBranch)`**：给定一个目标消息 ID，从 root 到目标消息的完整路径（这就是当前展示的对话线）
- **`getBranchChoicesAtMessage(nodeMap, messageId)`**：获取某条消息的所有兄弟节点（用于分支导航器 `< 1/3 >`）

### `stores/` — 状态仓库

#### `conversationStore.ts` — 对话状态

管理对话列表和当前对话的所有数据：

| 状态 | 说明 |
|------|------|
| `conversations` | 对话列表 |
| `current` | 当前选中的对话详情（含完整消息） |
| `nodeMap` | 消息树（`Map<id, MessageNode>`） |
| `branchPath` | 当前展示的分支路径（消息数组） |
| `activeBranch` | 当前分支末端的消息 ID |

关键动作：
- `setCurrent(conv)` — 设置当前对话时，自动构建消息树 + 提取分支路径
- `setActiveBranch(messageId)` — 切换分支时，重新计算路径

#### `streamStore.ts` — 流式状态

管理 SSE 流式传输过程中的实时数据：

| 状态 | 说明 |
|------|------|
| `isStreaming` | 是否正在流式传输 |
| `segments` | 执行段列表（每个 Agent 一段） |
| `pendingUserMessage` | 用户消息（在对话加载前先显示） |
| `completedSegments` | 已完成消息的段缓存（按 messageId 索引） |
| `permissionRequest` | 待确认的权限请求 |
| `error` | 错误信息 |

**ExecutionSegment（执行段）** 是流式渲染的核心概念：

```tsx
interface ExecutionSegment {
  agent: string;           // Agent 名称
  status: 'running' | 'complete';
  reasoningContent: string; // 思考过程
  isThinking: boolean;     // 是否正在思考
  toolCalls: ToolCallInfo[];  // 工具调用列表
  content: string;         // LLM 输出内容
}
```

一次完整的执行可能产生多个 Segment（Lead Agent → Search Agent → Lead Agent），UI 按 Segment 分段展示。

**性能优化**：`scheduleContentUpdate()` 使用 `requestAnimationFrame` 节流，避免每个 `llm_chunk` 事件都触发一次 Zustand 状态更新和 React 重渲染。

#### `artifactStore.ts` — 文稿状态

管理文稿面板的数据：

| 状态 | 说明 |
|------|------|
| `artifacts` | 文稿列表 |
| `current` | 当前选中的文稿详情 |
| `versions` | 版本列表 |
| `viewMode` | 查看模式：`preview` / `source` / `diff` |
| `pendingUpdates` | 流式过程中正在更新的文稿 ID |

#### `authStore.ts` — 认证状态

管理用户登录态：

| 状态 | 说明 |
|------|------|
| `token` | JWT access token |
| `user` | 当前用户信息（UserInfo） |
| `isAuthenticated` | 是否已登录 |
| `isHydrated` | 是否已从 localStorage 恢复状态 |

关键动作：
- `login(token, user)` — 登录成功后存储 token 和用户信息到 state + localStorage
- `logout()` — 清除所有认证状态和 localStorage
- `hydrate()` — 从 localStorage 同步恢复认证状态（应用启动时调用）

持久化 key：`af_token`、`af_user`（localStorage）。

#### `uiStore.ts` — UI 状态

最简单的 store，管理三个 UI 开关：

- `sidebarCollapsed` — 侧边栏折叠
- `artifactPanelVisible` — 文稿面板显示
- `theme` — 亮色 / 暗色主题（通过 `document.documentElement.classList.toggle('dark')` 切换）

### `hooks/` — 自定义 Hook

#### `useSSE.ts` — SSE 事件处理

**最复杂的 Hook**，负责把后端推送的 SSE 事件翻译成 store 状态更新。

事件处理流程：

```
后端推送事件 → connectSSE() 解析 → handleEvent() 分发 → 更新各 store
```

各事件的处理：

| 事件 | 处理 |
|------|------|
| `AGENT_START` | 创建新 Segment |
| `LLM_CHUNK` | 追加思考内容 / 输出内容（RAF 节流） |
| `LLM_COMPLETE` | 设置最终内容 |
| `TOOL_START` | 添加工具调用卡片 |
| `TOOL_COMPLETE` | 更新工具调用状态 + 如果是文稿工具则拉取文稿 |
| `AGENT_COMPLETE` | 标记 Segment 完成 |
| `PERMISSION_REQUEST` | 弹出权限确认弹窗 |
| `COMPLETE` | 快照 Segments → 刷新对话和文稿 |
| `ERROR` | 显示错误信息 |

文稿相关的特殊逻辑：当 `create_artifact` / `update_artifact` / `rewrite_artifact` 工具完成时，自动打开文稿面板并拉取最新内容。

#### `useChat.ts` — 聊天操作

封装了发消息的核心流程：

```
用户输入 → sendMessage() → POST /api/v1/chat → 拿到 stream_url → connectSSE()
```

处理三种发消息场景：
- **正常发送**：`parentMessageId` 为 undefined，自动用当前分支最后一条消息作为 parent
- **重跑（rerun）**：`parentMessageId` 为指定的消息 ID
- **新对话**：`conversation_id` 为空

#### `useArtifacts.ts` — 文稿操作

加载和管理文稿相关数据。

#### `useMediaQuery.ts` — 响应式断点

检测屏幕宽度，用于响应式布局（比如小屏自动折叠侧边栏）。

### `components/` — UI 组件

#### 认证组件

| 组件 | 作用 |
|------|------|
| `AuthGuard` | 路由保护组件，包裹需认证的页面。等待 `hydrate()` 完成后检查登录态，未登录则重定向到 `/login` |

#### 布局组件 (`layout/`)

| 组件 | 作用 |
|------|------|
| `ThreeColumnLayout` | 三栏布局容器，支持拖拽调整文稿面板宽度，处理响应式断点 |
| `PermissionModal` | 权限确认弹窗（Agent 请求执行受限工具时弹出） |

#### 侧边栏组件 (`sidebar/`)

| 组件 | 作用 |
|------|------|
| `Sidebar` | 侧边栏容器：新建对话按钮、对话列表、文稿切换、主题切换 |
| `ConversationList` | 对话列表，支持分页 |
| `ConversationItem` | 单条对话项（标题、时间、删除按钮） |

#### 聊天组件 (`chat/`)

| 组件 | 作用 |
|------|------|
| `ChatPanel` | 聊天面板主容器，三种状态切换：欢迎页 / 消息列表 / 流式新对话 |
| `MessageList` | 渲染当前分支路径上的所有消息 |
| `MessageInput` | 输入框 + 发送 / 上传按钮 |
| `UserMessage` | 用户消息气泡（支持编辑、重跑、分支导航） |
| `AssistantMessage` | 助手消息（展示 Agent 执行段 + 分支导航） |
| `StreamingMessage` | 流式响应（正在生成中的消息） |
| `AgentSegmentBlock` | 单个 Agent 执行段（思考 + 工具调用 + 输出） |
| `BranchNavigator` | 分支导航器 `< 1/3 >`，切换同一位置的不同回复 |
| `ToolCallCard` | 工具调用卡片（名称、参数、结果、耗时） |
| `ThinkingBlock` | 可折叠的思考过程块 |
| `AgentBadge` | Agent 名称标签 |

#### 文稿组件 (`artifact/`)

| 组件 | 作用 |
|------|------|
| `ArtifactPanel` | 文稿面板容器，路由到列表/详情视图 |
| `ArtifactList` | 文稿列表 |
| `ArtifactToolbar` | 顶部工具栏（返回、文稿信息、下载、版本选择） |
| `ArtifactTabs` | 视图切换标签（Preview / Source / Diff） |
| `MarkdownPreview` | Markdown 渲染预览 |
| `SourceView` | 源码查看 |
| `DiffView` | 版本差异对比 |

---

## 数据流全景

### 发送消息

```
用户输入文本
    │
    ▼
useChat.sendMessage(content)
    │
    ├─ POST /api/v1/chat ──────► 后端开始执行
    │   返回 { stream_url, conversation_id, message_id }
    │
    ├─ streamStore.startStream()  ← 设置流式状态
    ├─ streamStore.setPendingUserMessage()  ← 立即显示用户消息
    │
    └─ useSSE.connect(stream_url)
        │
        ▼
    GET /api/v1/stream/:thread_id （SSE 长连接）
        │
        ├─ event: agent_start    → pushSegment("Lead")
        ├─ event: llm_chunk      → scheduleContentUpdate(content)  [RAF 节流]
        ├─ event: tool_start     → addToolCallToSegment(...)
        ├─ event: tool_complete  → updateToolCallInSegment(...)
        ├─ event: agent_complete → updateCurrentSegment({ status: 'complete' })
        ├─ ...（可能循环多个 Agent）
        │
        └─ event: complete
            ├─ snapshotSegments()     ← 缓存执行段
            ├─ endStream()            ← 结束流式状态
            └─ refreshAfterComplete() ← 重新拉取对话 + 文稿数据
```

### 对话分支

```
消息A (user: "搜索 AI 新闻")
├── 消息B (assistant: 回复1)          ← 分支 1/2
│   └── 消息C (user: "详细说说")
│       └── 消息D (assistant: ...)
└── 消息E (assistant: 回复2)          ← 分支 2/2  [用户点了重跑]
    └── 消息F (user: "换个角度")

当前分支路径 branchPath = [A, E, F]
BranchNavigator 在消息 E 处显示 < 2/2 >，点击可切回 [A, B, C, D]
```

### 文稿更新

```
Agent 调用 create_artifact / update_artifact
    │
    ▼
SSE: tool_complete (tool_name = "create_artifact")
    │
    ├─ setArtifactPanelVisible(true)   ← 自动打开文稿面板
    ├─ addPendingUpdate(artifactId)     ← 标记正在更新
    └─ 并行拉取文稿详情 + 列表 + 版本
        │
        ▼
    文稿面板实时更新内容

SSE: complete
    └─ clearPendingUpdates()           ← 清除待更新标记
```

---

## 样式系统

使用 Tailwind CSS + 自定义设计系统（RAMS），在 `tailwind.config.ts` 中定义：

- **语义化颜色**：`text-primary`、`bg-surface`、`border-border` 等，每个颜色都有 dark 变体
- **暗色模式**：通过 `class` 策略，`<html class="dark">` 切换
- **组件样式**：`rounded-bubble`（消息气泡圆角）、`shadow-card`（卡片阴影）等

```tsx
// 典型用法：同时写亮色和暗色
<div className="bg-surface dark:bg-surface-dark text-text-primary dark:text-text-primary-dark">
```
