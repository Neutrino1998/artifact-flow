# 前端架构

> Next.js 15 App Router + Zustand + fetch/ReadableStream SSE — 三栏工作台，围绕"实时投射引擎状态"构建。

## Tech Stack

| 维度 | 选型 | 备注 |
|------|------|------|
| 框架 | Next.js 15 (App Router) | React 19 |
| 语言 | TypeScript 5.7（strict） | 类型从 OpenAPI 自动生成 |
| 样式 | Tailwind 3.4 + `@tailwindcss/typography` | class-based 暗色模式 |
| 状态 | Zustand 5 | 分 5 个 store，selector 精细订阅 |
| Markdown | `react-markdown` + `remark-gfm` + `rehype-highlight` | 代码高亮 |
| Diff | `diff` 8 | Artifact 版本对比 |
| 类型工具 | `openapi-typescript` | `npm run generate-types` |

**不使用** `@tanstack/react-virtual`——对话树长度实测不触发虚拟化收益，暂保持常规渲染；将来长会话场景可再引入。

## 目录结构

```
frontend/src/
├── middleware.ts               # 每请求生成 CSP nonce + 安全响应头（见「安全响应头与 CSP」）
├── app/
│   ├── layout.tsx              # Root layout + ThemeInitializer + 把 nonce 盖到内联脚本
│   ├── page.tsx                # 主聊天页（外层包 AuthGuard）
│   └── login/page.tsx
├── components/
│   ├── AuthGuard.tsx           # 鉴权水合 + must_change_password 强制改密闸门
│   ├── chat/                   # 消息、流式、工具卡片、admin 面板
│   ├── artifact/               # 面板、Tabs、预览 / Source / Diff
│   ├── layout/                 # ThreeColumnLayout、Modals、ChangePasswordDialog
│   ├── sidebar/                # 对话列表、用户菜单
│   └── markdown/CodeBlock.tsx
├── stores/                     # 5 个 Zustand store
├── hooks/                      # useSSE / useChat / useArtifacts / useMediaQuery
├── lib/                        # sse.ts / api.ts / messageTree.ts / csp.ts / passwordPolicy.ts …
└── types/
    ├── api.d.ts                # 自动生成（不要手改）
    ├── events.ts               # StreamEventType 镜像 + 各事件 data 接口
    └── index.ts                # 从 api.d.ts 抽取的常用类型 re-export
```

## 三栏布局

`components/layout/ThreeColumnLayout.tsx` 固定三栏，宽度由 `uiStore` 控制折叠：

```
┌──────────┬──────────────────────┬──────────────┐
│ Sidebar  │        Chat          │  Artifacts   │
│          │                      │              │
│ 对话列表 │  MessageList         │  ArtifactTabs│
│ 用户菜单 │  ProcessingFlow      │  Preview/    │
│          │  MessageInput        │  Source/Diff │
└──────────┴──────────────────────┴──────────────┘
```

- **Sidebar**：`ConversationList` + `UserMenu`；admin 可打开 `AdminConversationList`（只读 observability，不删数据）/ `ObservabilityPanel` / `UserManagementPanel`
- **Chat**：`ChatPanel` 组合 `MessageList`（含分支导航 `BranchNavigator`）+ `MessageInput`；流式期间由 `ProcessingFlow` 渲染各 agent 的 `AgentSegmentBlock` / `ToolCallCard` / `ThinkingBlock` / `CompactionFlowBlock` / `InjectFlowBlock` / `ErrorFlowBlock`
- **Right panel — mode-aware**：`userManagementVisible && isAdmin` 时右栏从 `ArtifactPanel` 切换为 `UserManagementDetailPanel`，按 `userManagementRightView` 类型分发到 `UserDetailForm` / `CreateUserForm` / `BulkImportForm` / `BulkActionPanel` / `DepartmentManagerPanel`；退出用户管理模式自动恢复 `ArtifactPanel`
- **Artifacts**：`ArtifactPanel` → `ArtifactTabs` → `MarkdownPreview | SourceView | DiffView`，顶栏 `ArtifactToolbar` 提供版本切换与 DOCX 导出

## 状态管理（Zustand Stores）

5 个 store 各管一个职责维度：

### `authStore`

| State | 说明 |
|-------|------|
| `token`, `user`, `isAuthenticated` | JWT 与当前用户（`user.must_change_password` 驱动强制改密闸门） |
| `isHydrated` | localStorage 恢复完成标志（避免 SSR mismatch） |

Actions：`login(token, user)`, `logout()`, `hydrate()`, `setUser(user)`（改密成功 / `/me` 刷新后就地更新用户，使 `must_change_password` 清除后无需重登）。

> **强制改密闸门**：`AuthGuard`（包在主页外层）水合后拉 `/me`；当 `user.must_change_password` 为 True 时，挡住整个应用、只渲染不可关闭的 `ChangePasswordDialog forced`（禁用 backdrop / Esc / 取消按钮）。这是与后端 403 闸门对称的前端侧；改密成功 `setUser` 清标志后恢复正常。表单的客户端口令强度提示来自 `lib/passwordPolicy.ts`（镜像后端 `PASSWORD_MIN_LENGTH` + 字母/数字/符号，仅提示不阻断，最终以后端校验为准）。

### `conversationStore`

| State | 说明 |
|-------|------|
| `conversations[]`, `current` | 列表与当前对话详情 |
| `listLoading`, `currentLoading` | 分别的 loading 标志 |
| `nodeMap`, `branchPath`, `activeBranch` | 消息树结构（由 `lib/messageTree.ts` 构建） |

消息树通过 `parent_id` 形成 DAG，`branchPath` 是从根到 `activeBranch` 叶子的路径；切换分支只改 `activeBranch` 和重算 `branchPath`。

### `artifactStore`

| State | 说明 |
|-------|------|
| `sessionId`, `artifacts[]`, `current` | 当前 session 的 artifact 列表与选中项 |
| `versions[]`, `selectedVersion` | 版本列表（对应 `GET .../versions/{v}`） |
| `viewMode` | `preview` / `source` / `diff` |
| `diffBaseContent` | diff 模式的基线 |
| `pendingUpdates[]` | 引擎执行中来自 `tool_complete.metadata.artifact_snapshot` 的增量覆盖 |
| `uploading`, `uploadError` | 上传态 |

`pendingUpdates` 是"DB 还未 flush、但 SSE 已推过来"的中间态（见 [architecture/artifacts.md](architecture/artifacts.md)）—— 执行终止后由 `clearPendingUpdates()` 清掉，REST 再次拉取拿到 flush 后的权威数据。

### `uiStore`

| State | 说明 |
|-------|------|
| `sidebarCollapsed`, `artifactPanelVisible` | 布局折叠 |
| `conversationBrowserVisible`, `userManagementVisible`, `observabilityVisible` | Admin 面板互斥可见性（同时只能开一个） |
| `userManagementRightView` | 右面板内容（discriminated union）：`empty` / `create-user` / `edit-user` / `bulk-import` / `bulk-action` / `dept-manager` |
| `userMgmtListVersion` | 右面板表单成功后 bump，触发 `UserManagementPanel` refetch（避免 prop 钻透） |
| `selectionMode`, `userManagementSelection` | 用户管理多选模式开关 + 选中 ID 列表；进入 `enterSelectionMode()` 自动把 RightView 切到 `bulk-action`，退出时回 `empty` |
| `observabilitySelectedConvId`, `observabilityBrowseVisible`, `observabilityRefreshTick` | Admin 观测面板：选中 conv id / 完整列表浏览 / 轮询触发 tick |
| `theme` | `light` / `dark`，持久化到 localStorage |

**面板切换协调**（避免状态泄漏）：`setUserManagementVisible(false)` / 切到其他 admin 面板会同时 reset `userManagementRightView` + `selectionMode` + `userManagementSelection`，保证下次进入是干净状态。

### `streamStore`

整个实时引擎的投影：

| State | 说明 |
|-------|------|
| `isStreaming`, `streamUrl`, `messageId`, `conversationId` | 连接态 |
| `segments[]` | `ExecutionSegment`：每个 agent 一段（agent, content, toolCalls, tokenUsage） |
| `nonAgentBlocks[]` | 非 agent 事件（inject / compaction / error） |
| `executionMetrics` | 从 `complete` 事件提取 |
| `permissionRequest` | 等待用户审批的当前项（null 表示无） |
| `cancelled`, `reconnecting`, `error` | 终端/中间态 |
| `cancelling` | cancel 已请求、引擎尚未确认终止 —— 驱动 Stop 按钮 "cancelling…" spinner（action `setCancelling`） |
| `queuedInfo` | `{ ahead, maxConcurrent } \| null`：执行排队等并发信号量时的位置；`null` = 已进入 RUNNING 或无执行。由 `execution_queued` 事件置位、`agent_start` 清除 |

关键 action：`appendCurrentSegmentContent(chunk)` 是 `llm_chunk` 的入口，内部走 RAF 节流（见下文性能小节）。

## SSE 集成

`lib/sse.ts` 的 `connectSSE(url, handlers, signal, lastEventId)`：

```typescript
const res = await fetch(url, {
  headers: {
    'Authorization': `Bearer ${token}`,
    ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
  },
  signal,
});
const reader = res.body!.getReader();
const decoder = new TextDecoder();
let buffer = '';
let currentEvent = '', currentId = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop()!;                  // 最后一行可能不完整，留在 buffer
  for (const line of lines) {
    if (line.startsWith('event:')) currentEvent = line.slice(6).trim();
    else if (line.startsWith('id:')) currentId = line.slice(3).trim();
    else if (line.startsWith('data:')) {
      const parsed = JSON.parse(line.slice(5).trim()) as SSEEvent;
      handlers.onEvent(parsed);
      connection.lastEventId = currentId;
    }
  }
}
```

**为什么不是 EventSource**：EventSource 不支持自定义 header，而后端强制 `Authorization: Bearer`（详见 [architecture/streaming.md → Design Decisions](architecture/streaming.md#为什么选-fetch--readablestream-而非-eventsource)）。

**`useSSE` hook** 在 `connectSSE` 之上加了业务分发：

- 按 `event.type` 分派到 `streamStore` 的对应 action
- `llm_chunk` 走 RAF 节流
- `execution_queued`（SSE-only 类型，见 `types/events.ts`）→ 置 `queuedInfo`；`agent_start` 时清除。排队中 `MessageInput` 的统一发送/停止/注入按钮被禁用——排队态既不可 cancel 也不可 inject（两端点在进入 RUNNING 前返回 409）
- 终端事件（`complete` / `cancelled` / `error`）触发 `endStream()` 并清理资源
- 自动重连：非终端断开时最多 3 次，指数退避 1s / 2s / 4s，携带 `lastEventId`
- 共享 `AbortController` 防止切换对话时出现孤立连接

Tool 事件副作用：`create_artifact` / `update_artifact` / `rewrite_artifact` 的 `tool_complete` 会触发 `uiStore.setArtifactPanelVisible(true)` 并把 `metadata.artifact_snapshot`（如果有）覆盖到 `artifactStore.pendingUpdates`；没有 snapshot 则走 REST 拉取。

## 性能优化

### RAF 节流 `llm_chunk`

每个 token 都触发一次 React state 更新会把对话窗口卡死。`streamStore` 用模块级 `_rafId` / `_pendingContent` 缓存增量：

```typescript
function scheduleContentUpdate(chunk: string) {
  _pendingContent += chunk;
  if (_rafId !== null) return;
  _rafId = requestAnimationFrame(() => {
    _appendFn?.(_pendingContent);
    _pendingContent = '';
    _rafId = null;
  });
}
```

效果：无论 LLM 每秒吐 5 / 50 / 500 个 chunk，组件重渲染被限制到约 60fps。

### Zustand selector 精细订阅

所有组件都用 `useStore(state => state.specificField)` 或 `useStore(useShallow(...))`，避免整 store 订阅；`segments[]` 更新不会触发 `MessageInput` 重渲染。

### `React.memo` 消息组件

`AssistantMessage` / `UserMessage` / `ToolCallCard` 全部 memo；`segments[]` 是不可变数组（每次更新替换 ref），配合 memo 让非当前 streaming 的历史段落不重算。

## 类型同步

后端 OpenAPI → 前端 `types/api.d.ts`：

```bash
# 后端（项目根）
python scripts/export_openapi.py    # 输出 frontend/openapi.json

# 前端
cd frontend && npm run generate-types   # openapi-typescript 生成 api.d.ts
```

**约定**：每次改后端 schema（路由、request/response model）后必须跑一遍，PR 包含 `api.d.ts` 的 diff。`types/index.ts` 从 `api.d.ts` re-export 常用类型给业务层使用，不要直接 import 路径 `components["schemas"]["..."]` 形式。

**`types/events.ts`** 是**手写**的（不自动生成）——SSE 事件不进 OpenAPI schema，为了与后端 `StreamEventType` 保持对齐，需要在后端新增事件类型时同步更新此文件。

## 暗色模式

- Tailwind `darkMode: 'class'`；`uiStore.theme` 切换时把 `dark` class 加到 `<html>`
- `ThemeInitializer` 在 root layout 用 `useEffect` 从 localStorage 读取初值，避免 SSR flash
- 品牌色从 Tailwind config 的 `theme.extend.colors` 提取（见 `tailwind.config.ts`）：
  - `accent.DEFAULT = #c96442`（主品牌色）
  - `chat.light = #FAF9F6` / `chat.dark = #1e1e1e`
  - status 色：`success #4a8c6f` / `error #c25d4e` / `warning #c49a3c`

文档站（MkDocs Material）的 CSS 从同一组 token 派生，见 `docs/stylesheets/custom.css`。

## 安全响应头与 CSP

`src/middleware.ts`（Next middleware，每请求执行）+ `src/lib/csp.ts` 注入 Content-Security-Policy 与一组静态加固头：

- **每请求随机 nonce**：middleware 生成 base64 nonce 写进 `script-src 'nonce-<nonce>' 'strict-dynamic'`，并经 `x-nonce` 请求头传给 `app/layout.tsx`，盖到那条内联主题 bootstrap 脚本上（暗色无闪烁那段）。**CSP 放 Next 而非 nginx**：nonce 必须与渲染同源生成，nginx 看不到它、只能退回 `'unsafe-inline'`，失去意义。
- **`connect-src` 从 `NEXT_PUBLIC_API_URL` 派生**：prod 同源部署时为空 → 退回 `'self'`；配置独立后端域时把该 origin 显式列入（dev 放宽）。
- **`img-src 'self' data: blob:`**（**刻意不含 `https:`**）：作为补偿控制收窄信标外泄面；导出 / 预览用图走 data/blob。
- `frame-ancestors 'none'` 防点击劫持；另有静态头 `X-Frame-Options: DENY` / `X-Content-Type-Options` / `Referrer-Policy` / `Permissions-Policy`。

## REST Client 缓存

`lib/api.ts` 做了两件事防抖：

- `listConversations()` 结果缓存 20s（切对话时来回点击不重复发请求）
- `getConversation(id)` 做 in-flight dedup（同一 id 的并发调用共享 Promise）

写操作（send / inject / cancel / resume / upload / delete）不缓存；写成功后业务层自行失效列表缓存。

## Design Decisions

### 为什么 Zustand 而非 Redux / Context

- 不需要时光回溯 / devtools 级别复杂度；selector-based 订阅天然适配 60fps 流式更新
- 5 个正交 store 比单一大 store 更利于代码分隔与类型推导
- Context + reducer 在流式高频更新下 re-render 失控，Zustand 的 `subscribe` 粒度可控

### 为什么类型手写一部分（`events.ts`）

- SSE 事件 schema 不在 OpenAPI 里（HTTP 响应体是 `text/event-stream`，OpenAPI 3.0 表达不了逐事件结构）
- 强行把事件塞进 schema 会让 `api.d.ts` 变得脏且难维护
- 手写 + 文档 + 后端 emitter 约定是更务实的权衡；新增事件类型是低频动作

### 为什么共享 `streamStore` 而非每消息独立

- **前端视角是单当前对话**：UI 任意时刻只聚焦在一个对话上，所以"正在流式的 SSE 连接"在单个浏览器标签内唯一；后端 lease 是**按 `conversation_id`** 的，多对话可并发执行（见 [architecture/concurrency.md → RuntimeStore](architecture/concurrency.md#runtimestore)），但前端不会同时渲染多条实时流
- 切对话时 `reset()` 全部 stream 态，避免跨对话状态污染；同一用户多 tab 打开不同对话由浏览器天然隔离（每 tab 独立 store 实例）
- 历史消息的"已完成 segment"快照通过 `snapshotSegments(messageId)` 下沉到 `streamStore.completedSegments` / `completedNonAgentBlocks` 两个 `Map<messageId, ...>`，`AssistantMessage` 直接按 `messageId` 从这两个 map 读取。这样流式状态与历史状态共用 store 但 key 空间分离，避免与 `conversationStore.current.messages[]` 的 DB 结构耦合

### 为什么不用 react-virtual

- 实测单次对话 message 数量（含分支）远低于需要虚拟化的门槛（千级）
- 引入 react-virtual 会和 Markdown 动态高度、代码块展开、分支折叠产生耦合
- 未来出现超长对话场景（如导入超大上下文）再按需引入，不预先埋
