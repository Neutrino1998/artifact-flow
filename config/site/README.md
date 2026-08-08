# Site config

前端运行时读取的两份纯静态 JSON，用于驱动：

- **欢迎页轮播提示**（`welcome_tips.json`）—— 新对话欢迎页副标题，8s 一条淡出淡入切换。
- **版权 / 问题反馈页脚**（`branding.json`）—— 侧栏底部 + 登录页底部的「由 X 开发 · 问题反馈」一行。

左栏通知已迁入共享数据库，由管理员 UI 编辑、由认证 API 提供给前端。
`notifications.example.json` 只保留字段示例，不再是运行时数据源。

## 部署 / 工作流

文件读盘位置取决于环境：

| 环境 | 物理路径 | 由谁服务 |
|---|---|---|
| Docker / 单机 afctl | host `control/site/*.json` → frontend 容器 `/app/public/site/*.json` | Next.js 容器服务欢迎提示与品牌静态文件 |
| 本地 `npm run dev` | `frontend/public/site/*.json` | Next.js dev server |

`config/site/` 只保存源码默认值和示例；运维修改欢迎提示或品牌时编辑目标机
`/opt/artifactflow/control/site/`。需要本地调试时手工 `cp` 一份到
`frontend/public/site/`。

`welcome_tips.json` 缺失或解析失败时回落到默认副标题；`branding.json` 缺失或
解析失败时隐藏页脚。**都不会阻塞前端启动**。

## 通知 schema（共享 DB）

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
- `dismissible: true` 的条目，用户点 × 后 ID 进入按用户隔离的浏览器
  `localStorage`；清理浏览器数据后可能再次显示（best effort）。
- 每个用户首次在该浏览器看到一个稳定通知 ID 时，详情 modal 自动弹出一次。
- 管理员 UI 保存时会校验 JSON schema、通知 ID 唯一性和 DB revision；如果配置在页面加载后被别人改过，会拒绝覆盖并提示刷新。
- 管理员 UI 可输入无时区的本地时间（如 `2026-05-15 00:00`）；后端按服务器本地时区解释并写回带 offset 的 ISO8601 字符串，避免浏览器按客户端时区误读。

## `welcome_tips.json` schema

```jsonc
[
  "切换到新话题时建议新建对话，让助手更专注当前任务",
  "截图可以直接粘贴到输入框，会自动作为附件发送",
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
