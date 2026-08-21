# PAT 与 API 调用

个人访问令牌（PAT）让脚本、CLI 或其他程序以某个 ArtifactFlow 用户的身份调用普通
用户 API。PAT 不是管理员凭证：即使所属用户是管理员，它也不能调用 Admin、部门、
密码、个人资料或 PAT 管理端点。

界面里的“API 密钥”就是 PAT，并不存在另一套独立 API Key。这里强调 PAT，是因为它
代表一个用户，并带有 scope、有效期和可撤销状态，而不是代表整个部署实例。

## 创建、使用和撤销

登录网页后，从用户菜单打开“API 密钥”。填写名称、有效期并选择权限范围。明文令牌
只显示一次；遗失后不能找回，只能撤销并重新创建。每个用户最多同时保有 50 个有效 PAT。

请求使用标准 Bearer Header，不要把令牌放在 URL：

```http
Authorization: Bearer af_pat_<id>_<secret>
```

撤销立即生效。用户被禁用、删除或被要求修改密码时，其 PAT 也不能继续调用业务接口。

## 权限范围

Scope 决定令牌可执行哪一类操作，资源所有权仍由 API 独立校验。Scope 之间没有隐式
继承，例如 `conversations:write` 不自动包含 `conversations:read`。

| Scope | 能力 |
|---|---|
| `conversations:read` | 完整对话、历史事件、活跃流和 SSE，可能包含 Artifact、工具与 reasoning 内容 |
| `conversations:write` | 发送消息、上传附件、引用文件和提交反馈 |
| `conversations:control` | 向运行中任务注入消息或取消任务 |
| `conversations:delete` | 单个或批量删除对话 |
| `artifacts:read` | 通过 Artifact API 读取列表、正文、版本和原始文件 |
| `skills:read` | 查看和导出用户可见的 Skill |
| `skills:write` | 导入、启停和删除用户自己的 Skill |
| `tools:approve` | 批准或拒绝工具调用，并可按工具在当前对话分支持续允许 |

Scope 是 API 能力入口，不是对话内部的内容过滤器。Artifact 正文可能出现在工具结果、
模型回复、reasoning 或事件中，所以 `conversations:read` 允许观察完整对话；
`artifacts:read` 单独控制 Artifact REST 端点。

## 发送消息和附件

PAT 复用 `POST /api/v1/chat` multipart 协议。`payload` 是 JSON 字符串，`files` 是
可重复的附件字段：

```bash
curl -X POST https://artifactflow.example/api/v1/chat \
  -H "Authorization: Bearer $ARTIFACTFLOW_PAT" \
  -F 'payload={"user_input":"分析这份报告","conversation_id":null}' \
  -F 'files=@report.pdf'
```

附件上传属于 conversation write，不存在独立的 artifact write scope。响应包含
`conversation_id`、`message_id` 和 `stream_url`。用同一 PAT 订阅 SSE：

```bash
curl -N \
  -H "Authorization: Bearer $ARTIFACTFLOW_PAT" \
  https://artifactflow.example/api/v1/stream/msg-123
```

浏览器客户端应使用 `fetch` 和 `ReadableStream`，因为原生 `EventSource` 不能可靠携带
Authorization Header。可携带 `Last-Event-ID` 重连；断开 SSE 不会自动取消任务或拒绝
正在等待的工具权限。

## 工具确认

需要确认的工具会通过 SSE 发出 `permission_request`，包含 `call_id`、`tool`、`params`
和平台生成的 `reason`。具有 `tools:approve` 的 PAT 可调用恢复端点：

```http
POST /api/v1/chat/{conversation_id}/resume
Authorization: Bearer <PAT>
Content-Type: application/json

{
  "message_id": "msg-123",
  "call_id": "call-123",
  "approved": true,
  "always_allow": true
}
```

`always_allow: false` 只批准当前调用；`always_allow: true` 会在当前对话分支持续允许
同名工具，后续参数即使不同也不再询问。请只对可信工具使用持续允许，并妥善保管具有
`tools:approve` 的 PAT。

## 不可调用的能力

PAT 不能调用：

- `/api/v1/admin/*`；
- `/api/v1/departments/*`；
- 修改密码或个人资料；
- 创建、列出或撤销 PAT；
- 管理平台 Tool 定义和凭证。

`GET /api/v1/auth/me`、`GET /api/v1/meta` 和用户通知允许任意有效 PAT 调用，用于身份
确认和客户端初始化；其余普通用户端点按 scope 检查。完整字段与响应结构以运行中服务的
OpenAPI 为准。

返回[产品使用指南](index.md)。
