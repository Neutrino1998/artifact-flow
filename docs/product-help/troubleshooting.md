# 常见使用问题

本页只处理用户和管理员在产品界面或公开 API 中能观察到的问题。不要在对话、截图或
工单中粘贴 PAT、密码、Tool 凭证或包含敏感参数的完整请求。

## Skill 看不到或没有生效

依次检查：

1. Skill 是否对当前用户 `public`，或当前部门是否获得了 `department` 权限；
2. 用户是否在“技能管理”中启用了该 Skill；
3. 是否存在同名个人 Skill 覆盖共享版本；
4. 当前问题是否符合 Skill 的用途；需要时可在输入框中主动选择。

Skill 能打开的 Tool 仍受 Agent 能力上限、Tool unit 可见性和执行确认约束。

## Tool 看得到但不能调用

- 确认 unit 已挂载给当前 Agent，或由当前激活的 Skill 提供。
- `department` unit 需要当前用户部门获得授权。
- 检查 unit 详情中的凭证是否显示“已配置”。不要把凭证值发到对话中排查。
- `confirm` Tool 会先等待用户批准；拒绝或超时后不会执行。
- `defer` 只改变参数何时披露，不会授予新 Tool。缺少 `search_tools` 时会回退为完整披露。

## 参数配置不可用

当前 JSON Schema 使用嵌套、组合、条件或其他高级约束时，参数配置无法无损表达，界面
会保留高级 JSON Schema 模式并显示具体原因。继续在 JSON 中编辑，或删除对应高级约束
后再切回参数配置。

## PAT 请求失败

| 状态 | 常见含义 |
|---|---|
| `401` | PAT 格式错误、已过期、已撤销，或所属用户已不可用 |
| `403` | PAT 缺少所需 scope，或该端点明确不接受 PAT |
| `404` | 资源不存在，或不属于当前用户 |
| `409` | 当前操作与同一对话中正在进行的任务冲突 |
| `422` | 请求字段、multipart payload 或参数值不符合接口定义 |

先用 `GET /api/v1/auth/me` 确认 PAT 有效，再核对端点所需 scope。不要通过聊天发送完整
Authorization Header。完整请求字段以运行中服务的 OpenAPI 为准。

## SSE 中断或工具一直等待

- SSE 断开不会自动取消任务，可以使用同一 PAT 和 `Last-Event-ID` 重连。
- 看到 `permission_request` 时，应使用网页批准，或由具有 `tools:approve` 的 PAT 调用
  resume 端点。
- 持续允许按当前对话分支和 Tool 名生效；若不确定参数是否可信，只批准当前调用。

## 请求支持时提供什么

提供发生时间、界面操作、端点路径、HTTP 状态码、request ID、conversation/message ID
以及已经尝试过的步骤。截图前遮盖用户内容和参数。不要提供 `.env`、Authorization Header、
PAT 明文、密码或 Tool 凭证。

返回[产品使用指南](index.md)。
