# Frontend

## 快速开始

```bash
npm install     # 安装依赖
npm run dev     # 启动开发服务器 → http://localhost:3000
npm run build   # 生产构建
npm run lint    # Lint 检查
```

后端 API 需要同时运行（默认 `http://localhost:8000`），在项目根目录执行 `python run_server.py`。

## 技术栈

- **框架**: Next.js 15 (App Router) / React 19
- **语言**: TypeScript
- **样式**: Tailwind CSS
- **状态管理**: Zustand
- **Markdown 渲染**: react-markdown + remark-gfm

## 目录结构

```
src/
├── app/            # App Router 页面和布局
│   └── login/     # 登录页
├── components/     # UI 组件（含 AuthGuard 路由保护）
├── stores/         # Zustand stores（conversation / artifact / stream / ui / auth）
├── hooks/          # 自定义 hooks（SSE 连接、分支导航等）
├── lib/            # 工具函数、API client
└── types/          # TypeScript 类型定义（含自动生成的 api.d.ts）
```

## 设计文档

| 文档 | 内容 |
|------|------|
| [UI 布局与交互](../docs/_archive/frontend/03_frontend_design.md) | 三栏布局、消息气泡、SSE 流式渲染、对话树分支、权限弹窗（设计阶段参考） |
| [视觉风格规范](../docs/_archive/frontend/DESIGN_SYSTEM.md) | 参考 Claude App 视觉风格：简约、人文、温暖的色调与留白（设计阶段参考） |
| [API 接口](../docs/api.md) | REST 端点、请求/响应 Schema、错误码 |
| [SSE 事件协议](../docs/streaming.md) | 事件类型、数据格式、连接生命周期 |

> **Note**: UI 布局与视觉风格文档位于 `docs/_archive/` 归档目录，为设计阶段的参考文档，实际实现可能有所调整。API 和 SSE 文档为当前维护的规范文档。

建议阅读顺序：先看 UI 布局与交互文档了解整体页面结构，再按需查阅 API 和 SSE 文档。

## API 类型同步

前端 TypeScript 类型从后端 FastAPI OpenAPI schema 自动生成，避免手动维护导致前后端类型不一致。

```bash
# 在项目根目录执行：导出 schema → 生成 TS 类型
python scripts/export_openapi.py
npm run generate-types
```

- 生成的文件：`src/types/openapi.json`（schema）、`src/types/api.d.ts`（TS 类型）
- `openapi.json` 提交到 git，方便 review schema 变更
- `api.d.ts` 也提交到 git，不需要每次安装后重新生成
- **何时执行**：后端修改了 `src/api/schemas/` 下的 Pydantic model 之后

使用方式：

```typescript
import type { components } from '@/types/api'
type Conversation = components['schemas']['ConversationDetailResponse']
```

## 开发注意事项

### 深色模式

`tailwind.config.js` 必须设置 `darkMode: 'class'`（非默认的 `media`），以支持用户手动切换。从第一个组件开始就写 `dark:` 变体，漏掉的组件会在深色模式下露出白块。

色板与 Light/Dark 对照值见 [DESIGN_SYSTEM.md](../docs/_archive/frontend/DESIGN_SYSTEM.md)（设计阶段参考，实际色值以 `tailwind.config.ts` 为准）。

### SSE 事件类型

后端 `src/core/events.py` 中的 `StreamEventType` 定义了所有事件类型。前端在 `src/types/` 中维护对应的枚举，事件处理的 switch 语句加 `default` 分支打 warning，以便发现后端新增了未处理的事件。

### 流式渲染性能

- `llm_chunk` 事件频率很高（每秒几十次），Markdown 渲染做节流（`requestAnimationFrame` 或 16ms），或流式阶段用纯文本 + 光标，`complete` 后再切 Markdown 渲染
- Zustand store 用 selector 细粒度订阅（`useStreamStore(s => s.streamContent)`），避免流式更新触发整个消息列表重渲染
- 历史消息组件用 `React.memo`，内容不变不重渲染

### SSE 连接管理

- **用 `fetch` + `ReadableStream` 而不是原生 `EventSource`**。原生 EventSource 不支持自定义 header，无法传 `Authorization: Bearer` token。`sse.ts` 从 `authStore` 读取 token 自动注入 auth header，401 响应触发登出
- 组件卸载时必须 abort fetch / close 连接，否则连接泄漏（浏览器同域 6 连接上限）
- 高频事件考虑批处理：攒几十毫秒的事件一次性更新 state

### 组件拆分

按 UI 设计文档的层级拆分，每个文件 150-200 行以内：

```
components/
├── layout/
│   ├── ThreeColumnLayout.tsx    # 三栏主布局
│   └── PermissionModal.tsx      # 工具权限确认弹窗
├── chat/
│   ├── ChatPanel.tsx            # 聊天面板容器
│   ├── MessageList.tsx          # 消息列表
│   ├── MessageInput.tsx         # 输入框
│   ├── UserMessage.tsx          # 用户消息气泡
│   ├── AssistantMessage.tsx     # 助手消息（含 Agent 分段）
│   ├── StreamingMessage.tsx     # 流式渲染中的消息
│   ├── AgentSegmentBlock.tsx    # Agent 执行段落
│   ├── AgentBadge.tsx           # Agent 名称标签
│   ├── ThinkingBlock.tsx        # 思考过程折叠块
│   ├── ToolCallCard.tsx         # 工具调用卡片
│   └── BranchNavigator.tsx      # 分支导航器
├── artifact/
│   ├── ArtifactPanel.tsx        # Artifact 面板容器
│   ├── ArtifactList.tsx         # Artifact 列表
│   ├── ArtifactTabs.tsx         # Preview/Source/Diff 标签页
│   ├── ArtifactToolbar.tsx      # Artifact 工具栏
│   ├── MarkdownPreview.tsx      # Markdown 预览
│   ├── SourceView.tsx           # 源码视图
│   └── DiffView.tsx             # Diff 对比视图
├── sidebar/
│   ├── Sidebar.tsx              # 侧边栏容器
│   ├── ConversationList.tsx     # 对话列表
│   ├── ConversationItem.tsx     # 对话列表项
│   ├── UserMenu.tsx             # 用户菜单（头像、主题切换、管理用户[Admin]、退出登录）
│   └── UserManagementModal.tsx  # 用户管理弹窗（仅 Admin）
└── ErrorBoundary.tsx            # 错误边界
```

### Store 设计

Zustand store 只放状态和简单 setter。业务逻辑放到 hooks 和 lib：

| 位置 | 职责 |
|------|------|
| `stores/` | 状态定义、简单 setter（含 `authStore` 管理登录态） |
| `hooks/useSSE.ts` | SSE 连接管理、事件分发 |
| `hooks/useChat.ts` | 发送消息、编辑、重跑等交互逻辑 |
| `hooks/useArtifacts.ts` | Artifact 加载与版本切换 |
| `hooks/useMediaQuery.ts` | 响应式断点检测 |
| `lib/api.ts` | API 调用封装 |
| `lib/sse.ts` | SSE 连接、事件解析 |
| `lib/messageTree.ts` | 消息树构建、分支路径提取 |

### 错误边界

在关键位置加 React Error Boundary，局部崩溃不影响全局：
- 消息列表级别：单条消息渲染失败不影响其他消息
- Artifact 面板级别：Diff 计算崩了不影响对话区

### 长列表优化

对话消息多了之后（几十轮），用虚拟列表（`@tanstack/react-virtual`）只渲染可视区域内的消息。注意消息高度不固定，需要动态高度模式。

## Docker

```bash
# 单独构建前端镜像
docker build -t artifactflow-frontend .

# 或在项目根目录用 docker compose 同时启动前后端
docker compose up
```
