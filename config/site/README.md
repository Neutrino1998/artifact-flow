# Site config

前端运行时读取的三份纯静态 JSON，用于驱动：

- **左栏通知**（`notifications.json`）—— UserMenu 上方的通知卡片，点击弹 modal 展开 markdown 详情。
- **欢迎页轮播提示**（`welcome_tips.json`）—— 新对话欢迎页副标题，5s 一条向左滑动切换。
- **版权 / 业务联系页脚**（`branding.json`）—— 侧栏底部 + 登录页底部的「由 X 开发 · email」一行。

## 部署 / 工作流

文件读盘位置取决于环境：

| 环境 | 物理路径 | 由谁服务 |
|---|---|---|
| Docker（任一 compose 文件） | host `config/site/*.json` → 容器 `/app/public/site/*.json` | Next.js 容器（standalone 服务 public/ 静态） |
| 本地 `npm run dev` | `frontend/public/site/*.json` | Next.js dev server |

两端各自独立维护。运维改 prod 时只动 `config/site/`，需要本地调试时手工 `cp` 一份到 `frontend/public/site/`。

文件缺失或解析失败时，对应 UI 组件自动隐藏（通知）或回落到默认副标题（欢迎页）。**不会阻塞前端启动**。

## `notifications.json` schema

```jsonc
[
  {
    "id": "maintenance-2026-05-20",      // 必填。稳定唯一 ID，前端用它做 dismiss 记忆 key。
    "severity": "warn",                  // 必填。"info" | "warn" | "critical"，控制小色块颜色。
    "title": "系统维护通知",              // 必填。列表里显示的标题。
    "body": "## 维护时间\n...",          // 必填。modal 里渲染的 markdown 正文。
    "starts_at": "2026-05-15T00:00:00Z", // 可选。ISO8601，早于此时间不展示。
    "ends_at": "2026-05-20T04:00:00Z",   // 可选。ISO8601，晚于此时间不展示。
    "dismissible": true                  // 可选，默认 true。false = 强制展示直到 ends_at 过期。
  }
]
```

- 多条同时生效时，左栏卡片显示**最高 severity 那条**的标题 + 一个"+N"角标。
- `dismissible: true` 的条目，用户点 × 后 ID 进入 `localStorage["af.dismissed_notifications"]`，再不展示（除非 ID 变了）。

## `welcome_tips.json` schema

```jsonc
[
  "文档左栏右上角"≡"图标可以点击回到列表",
  "可以拖拽 doc/txt/md/pdf/代码文件直接上传",
  "..."
]
```

纯字符串数组。空数组或文件不存在时欢迎页副标题回落到默认文案。

## `branding.json` schema

```jsonc
{
  "developer": "同温层",                    // 必填。"由 X 开发" 中的 X。
  "contact_email": "contact@example.com"   // 可选。给则渲染为可点击的 mailto 链接。
}
```

- 文件缺失 / 字段错位 / `developer` 为空 → 整个页脚隐藏（fail-closed）。删文件就能彻底关掉页脚。
- 应用名（`ArtifactFlow`）和副标题（`多智能体任务工作台`）不在这里——它们是 build-time 常量在 `frontend/src/lib/branding.ts`，因为 HTML `<title>` 是 Next.js server-side metadata，触达不到 runtime fetch。改这两项需要改代码 + 重新打镜像。
