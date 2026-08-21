# ArtifactFlow 产品使用指南

这组文档只解释用户和管理员在产品界面中可见的能力，不包含部署或内部运维信息。

## 角色与入口

| 角色 | 常见任务 | 主要入口 |
|---|---|---|
| 普通用户 | 对话、上传文件、查看 Artifact、启用或导入个人 Skill、创建 PAT | 对话页、用户菜单 |
| 管理员 | 用户与部门管理、共享 Skill、Tool unit/MCP、部门授权、会话观测 | 用户菜单中的管理工作台 |
| API 调用者 | 通过脚本发送对话、上传附件、读取 Artifact 或管理自己的 Skill | 普通用户创建的 PAT |

管理员也是普通用户，但管理员创建的 PAT 仍只代表其普通用户身份，不能调用 Admin、
部门、个人资料或 PAT 管理 API。

## 核心对象

| 概念 | 它解决什么 | 不是什么 |
|---|---|---|
| Conversation | 一条可继续或分支的任务历史，包含 Message 和执行事件 | 不是 Artifact 文件夹 |
| Artifact | 对话中上传或 Agent 生成的可版本化材料 | 不等于聊天回复 |
| Agent | 长期的角色、Model 和能力上限 | 不是一次性操作手册 |
| Skill | 按需加载的方法、领域说明和可选资源 | 不是一个 HTTP endpoint |
| Tool | Model 可调用的一项操作 | 不是多步工作流 |
| Tool unit | Tool 的管理、可见性、凭证、成员关系与披露单元 | 不一定只含一个 Tool |
| PAT | 用于脚本和 CLI 的带 scope 普通用户凭证 | 不是 Admin 会话 |

## 权限不是一个总开关

遇到“为什么看得到但不能调用”时，应分别检查：

| 权限轴 | 回答什么问题 | 在哪里设置 |
|---|---|---|
| 登录角色 | 能否进入 Admin 管理 API 和界面 | 用户角色；PAT 不继承 Admin |
| 资源可见性 | 这个部门的用户能否使用 Skill 或 Tool unit | `public` / `department` 与部门授权 |
| Agent/Skill 成员关系 | 当前 Agent 或激活的 Skill 是否拥有这个 Tool unit | Agent/Skill 的 Tool unit 配置 |
| Tool 执行权限 | 调用具体 Tool 时自动执行还是要求确认 | Tool 成员的 `auto` / `confirm` |
| PAT scope | 脚本凭证能否进入某类普通用户 API | 创建 PAT 时选择的 scope |

这些权限会叠加而不会互相替代。例如，部门可见的 Tool unit 仍需配置给当前 Agent；
`tools:approve` 也只允许 PAT 回应自己对话里的待批准调用，不会授予新的 Tool。

## 常见选择

- 教 Agent “怎么完成一类任务”：使用 Skill。真正创建或检查 Skill 包时，在对话中要求
  `skill-creator` 协助。
- 让 Model 调用一个外部 HTTP 操作：创建单工具。同一服务有多个相关操作时使用工具集。
- 外部服务已经提供 MCP `streamable_http`：配置 MCP Server。
- 从程序调用普通用户 API：创建 PAT，并只选择需要的 scope。

## 继续阅读

- [Tool 与 MCP 管理](tools.md)
- [Skill 使用与管理](skills.md)
- [PAT 与 API 调用](pat-api.md)
- [常见使用问题](troubleshooting.md)
