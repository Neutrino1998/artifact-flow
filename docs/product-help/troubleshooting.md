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

同一状态码在不同端点可能对应不同业务原因。下表用于初步分类；具体原因优先阅读响应
`detail`，再查看对应端点的 OpenAPI。

| 状态 | 常见含义 |
|---|---|
| `400` | 请求已被理解，但违反当前端点的业务规则；具体原因以响应 `detail` 为准 |
| `401` | PAT 格式错误、已过期、已撤销、所属用户已不可用，或该端点只接受网页登录会话 |
| `403` | PAT 有效但缺少所需 scope，或账号当前必须先修改密码 |
| `404` | 资源不存在，或不属于当前用户 |
| `409` | 资源当前状态与操作冲突；具体原因以响应 `detail` 为准 |
| `413` | 上传附件或导入 Skill 将超过用户存储配额 |
| `422` | HTTP 请求结构或提交内容未通过端点校验；具体字段以响应 `detail` 为准 |

先调用 `GET /api/v1/auth/me`：如果它也返回 `401`，应检查 PAT 本身；如果它成功、目标
端点却返回 `401`，目标端点只接受网页登录会话，增加 scope 无法解决。目标端点返回
`403` 时，再核对 scope 或登录网页完成密码修改。不要通过聊天发送完整 Authorization
Header。完整请求字段以运行中服务的 OpenAPI 为准。

## HTTP 响应与 SSE 执行事件

`POST /api/v1/chat` 返回成功只表示对话任务已提交。之后的 Agent 和 Tool 执行结果通过
SSE 返回，不会改写已经完成的 HTTP 响应：

- HTTP 非 2xx：API 操作没有正常完成，先检查响应 `detail`；
- `tool_complete` 的 `success` 为 `false`：Tool 参数未通过校验或 Tool 执行失败，查看
  事件中的安全错误信息；
- `error`、`cancelled` 或 `timed_out` 终态事件：对话任务已进入执行阶段，但未正常完成。

因此，模型生成的 Tool 参数不符合 Schema 时，通常不会让先前的聊天请求返回 HTTP
`422`，而会形成失败的 `tool_complete` 事件。

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
