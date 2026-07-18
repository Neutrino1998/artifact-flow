# Site config

前端运行时读取的三份纯静态 JSON，用于驱动：

- **左栏通知**（`notifications.json`）—— UserMenu 上方的通知卡片，点击弹 modal 展开 markdown 详情。
- **欢迎页轮播提示**（`welcome_tips.json`）—— 新对话欢迎页副标题，5s 一条向左滑动切换。
- **版权 / 问题反馈页脚**（`branding.json`）—— 侧栏底部 + 登录页底部的「由 X 开发 · 问题反馈」一行。

## 部署 / 工作流

文件读盘位置取决于环境：

| 环境 | 物理路径 | 由谁服务 |
|---|---|---|
| Docker / afctl | host `control/site/*.json` → frontend 容器 `/app/public/site/*.json`；backend 容器 `/app/site-config/*.json` | Next.js 容器服务静态文件；admin API 只写通知文件 |
| 本地 `npm run dev` | `frontend/public/site/*.json` | Next.js dev server |

两端各自独立维护。`config/site/` 只保存源码默认值和示例；运维改生产时可在管理员菜单进入「通知管理」写 `notifications.json`，也可直接编辑目标机 `/opt/artifactflow/control/site/`。需要本地调试时手工 `cp` 一份到 `frontend/public/site/`。

文件缺失或解析失败时，对应 UI 组件自动隐藏（通知）或回落到默认副标题（欢迎页）。**不会阻塞前端启动**。

## `notifications.json` schema

```jsonc
[
  {
    "id": "maintenance-2026-05-20",      // 必填。稳定唯一 ID，前端用它做 dismiss 记忆 key。
    "severity": "warn",                  // 必填。"info" | "warn" | "critical"，控制小色块颜色。
    "title": "系统维护通知",              // 必填。列表里显示的标题。
    "body": "## 维护时间\n...",          // 必填。modal 里渲染的 markdown 正文。
    "starts_at": "2026-05-15T00:00:00+08:00", // 可选。ISO8601，早于此时间不展示。
    "ends_at": "2026-05-20T04:00:00+08:00",   // 可选。ISO8601，晚于此时间不展示。
    "dismissible": true                  // 可选，默认 true。false = 强制展示直到 ends_at 过期。
  }
]
```

- 多条同时生效时，左栏卡片显示**最高 severity 那条**的标题 + 一个"+N"角标。
- `dismissible: true` 的条目，用户点 × 后 ID 进入 `localStorage["af.dismissed_notifications"]`，再不展示（除非 ID 变了）。
- 管理员 UI 保存时会校验 JSON schema、通知 ID 唯一性和 revision；如果文件在页面加载后被别人改过，会拒绝覆盖并提示刷新。
- 管理员 UI 可输入无时区的本地时间（如 `2026-05-15 00:00`）；后端按服务器本地时区解释并写回带 offset 的 ISO8601 字符串，避免浏览器按客户端时区误读。

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
  "feedback": {                            // 可选。不填则只显示开发方。
    "label": "问题反馈",                    // 必填。链接展示文案。
    "href": "mailto:contact@example.com"   // 必填。支持 mailto: / http: / https:。
  }
}
```

- 文件缺失 / 字段错位 / `developer` 为空 → 整个页脚隐藏（fail-closed）。删文件就能彻底关掉页脚。
- 反馈入口可以指向邮箱或共享文档：邮箱写 `mailto:contact@example.com`；共享文档写完整 `https://...` 地址。
- 旧字段 `contact_email` 不再支持；需要邮箱入口时统一写到 `feedback.href`。
- 应用名（`ArtifactFlow`）和副标题（`多智能体任务工作台`）不在这里——它们是 build-time 常量在 `frontend/src/lib/branding.ts`，因为 HTML `<title>` 是 Next.js server-side metadata，触达不到 runtime fetch。改这两项需要改代码 + 重新打镜像。
