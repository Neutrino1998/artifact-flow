# ArtifactFlow 产品使用指南

这一页帮助用户判断一个问题应该在哪里处理。具体的 REST 字段以运行中服务的
OpenAPI 为准；这里解释产品概念、界面操作和能力边界。

## 先判断你在做什么

| 角色 | 常见任务 | 主要入口 |
|---|---|---|
| 普通用户 | 对话、上传文件、查看 Artifact、启用或导入个人 Skill、创建 PAT | 对话页、用户菜单 |
| 管理员 | 用户与部门管理、共享 Skill、Tool unit/MCP、部门授权、会话观测 | 用户菜单中的管理工作台 |
| 部署运维 | Model/Agent 种子配置、Release、TLS、PostgreSQL/Redis、Sandbox 与故障处理 | Wiki 的配置与部署章节 |

管理员也是普通用户，但管理权限只来自网页登录会话。管理员创建的 PAT 仍只是
普通用户凭证，不能调用 Admin 或部门管理 API。

## 核心对象

| 概念 | 它解决什么 | 不是什么 |
|---|---|---|
| Conversation | 一条可分支的任务历史，包含 Message 和执行事件 | 不是 Artifact 文件夹 |
| Artifact | 对话中上传或 Agent 生成的可版本化材料 | 不等于聊天回复 |
| Agent | 长期的角色、Model 和能力上限 | 不是一次性操作手册 |
| Skill | 按需加载的方法、领域说明和可选资源 | 不是一个 HTTP endpoint |
| Tool | Model 可调用的一项可授权操作 | 不是多步工作流 |
| Tool unit | Tool 的管理、可见性、凭证、成员关系与披露单元 | 不一定只含一个 Tool |
| PAT | 用于脚本和 CLI 的带 scope 普通用户凭证 | 不是 Admin 会话 |

## 权限不是一个总开关

ArtifactFlow 把不同目的的权限分开管理，遇到“为什么看得到但不能调用”时，应按下面
几根轴分别检查：

| 权限轴 | 回答什么问题 | 在哪里设置 |
|---|---|---|
| 登录角色 | 能否进入 Admin 管理 API 和界面 | 用户角色；PAT 不继承 Admin |
| 资源可见性 | 这个部门的用户能否看到 Skill 或 Tool unit | `public` / `department` 与部门授权 |
| Agent/Skill 成员关系 | 当前 Agent 或激活的 Skill 是否拥有这个 Tool unit | Agent/Skill 的 Tool unit 配置 |
| Tool 执行权限 | 调用一个具体 Tool 时是自动执行还是要求确认 | 成员的 `auto` / `confirm` |
| PAT scope | 脚本凭证能否进入某类普通用户 API | 创建 PAT 时选择的 scope |

这些轴会叠加而不会互相替代。例如，部门可见的 Tool unit 仍需配置给当前 Agent；
`tools:approve` 也只允许 PAT 回应自己对话里的待批准调用，不会让它获得新的 Tool。

## 常见选择

- 要教 Agent “怎么做一类任务”：写 Skill。要真正创建和测试 Skill 包，在对话中要求
  “帮我创建/体检 Skill”，由 `skill-creator` 处理。
- 要让 Model 调用外部 REST 操作：创建 HTTP Tool。同一服务有多个相关 endpoint 时，
  把它们放进一个 Toolset。
- 外部服务已提供 MCP `streamable_http`：配置 MCP Server，不要再手工重建每个 endpoint。
- 要在程序中发送对话、上传附件或读取 Artifact：创建 PAT，并只选需要的 scope。

## 从哪里继续

- 管理 Tool unit、输入参数与 Secret：[Tool 与 MCP 配置](configuration/tools.md)。
- 启用、导入、分享或创建 Skill：[Skill 配置](configuration/skills.md)。
- 从脚本调用普通用户 API：[PAT 与 API 调用](personal-access-tokens.md)。
- 理解一次对话如何运行：[ArtifactFlow 如何工作](how-it-works.md)。
- 已部署系统出现异常：[故障处理](operations/troubleshooting.md)。
