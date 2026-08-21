# PAT 与 API 调用

个人访问令牌（PAT）让脚本、CLI 或其他程序以某个 ArtifactFlow 用户的身份调用普通
用户 API。PAT 不是管理员凭证：即使令牌所属用户是管理员，它也只能访问该用户自己的
对话、文件和 Skill，不能调用 Admin、部门、密码、资料或 PAT 管理端点。

## 创建和撤销

登录网页后，在左下角用户菜单打开“API 密钥”。创建时填写名称、有效期并选择权限。
明文令牌只显示一次，服务端只保存 HMAC-SHA256 校验值；遗失后不能找回，只能撤销并
重新创建。撤销立即生效，用户被禁用、删除或被要求修改密码时，PAT 也不能继续调用
业务接口。

每个用户最多同时保有 50 个有效 PAT。用户列表只显示尚未撤销且未过期的密钥；
撤销或过期记录仍保留在服务端作为安全审计记录，但不再占用有效 PAT 名额。

请求使用标准 Bearer Header，不要把令牌放在 URL：

```http
Authorization: Bearer af_pat_<id>_<secret>
```

## 权限范围

Scope 决定令牌可执行哪一类操作，资源所有权仍由现有 API 独立校验。具有
`conversations:read` 的 PAT 只能读取所属用户自己的对话，跨用户资源仍返回 404。

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

网页登录 JWT 保持完整的普通用户能力；PAT 没有隐式 scope 继承，例如
`conversations:write` 不自动包含 `conversations:read`。

Scope 控制 API 能力入口，不是同一用户数据之间的内容隔离边界。Artifact 正文可能被工具结果、
模型回复、reasoning 或事件复制到对话流中；因此 `conversations:read` 允许观察完整对话内容，
而 `artifacts:read` 单独控制 Artifact REST 端点。

## 发送消息和附件

PAT 复用现有 `POST /api/v1/chat` multipart 协议。`payload` 是 JSON 字符串，`files`
是可重复的附件字段：

```bash
curl -X POST https://artifactflow.example/api/v1/chat \
  -H "Authorization: Bearer $ARTIFACTFLOW_PAT" \
  -F 'payload={"user_input":"分析这份报告","conversation_id":null}' \
  -F 'files=@report.pdf'
```

附件上传属于一次 conversation write，不存在独立的 artifact 写端点，也不需要
`artifacts:write`。服务端转换完整批次、检查存储配额后，在 turn 内把附件 stage 成
artifact；Agent 工具创建或更新的 artifact 同样属于执行结果。

响应包含 `conversation_id`、`message_id` 和 `stream_url`。用同一 PAT 订阅 SSE：

```bash
curl -N \
  -H "Authorization: Bearer $ARTIFACTFLOW_PAT" \
  https://artifactflow.example/api/v1/stream/msg-123
```

浏览器客户端应使用 `fetch` 和 `ReadableStream`，因为原生 `EventSource` 不能可靠携带
Authorization Header。断开 SSE 不会自动拒绝正在等待的工具权限；客户端可携带
`Last-Event-ID` 重连。

## 工具确认

需要确认的工具会通过 SSE 发出 `permission_request`，其中包含 `call_id`、`tool`、
`params` 和平台生成的 `reason`。具有 `tools:approve` 的 PAT 可调用现有恢复端点：

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

服务端同时校验 conversation、message 和当前待处理 `call_id`。`always_allow: false`
只批准当前调用；`always_allow: true` 会把该工具名加入当前对话分支的允许集合，
后续同名工具调用即使参数不同也不再询问。PAT 与网页会话使用相同语义，服务端不再按凭证类型
额外限制。请只对可信工具使用持续允许，并妥善保管具有 `tools:approve` 的 PAT。

## 不可调用的能力

PAT 不能调用以下端点：

- `/api/v1/admin/*`；
- `/api/v1/departments/*`；
- 修改密码或个人资料；
- 创建、列出或撤销 PAT；
- 管理平台工具定义和工具凭证。

`GET /api/v1/auth/me`、`GET /api/v1/meta` 和用户通知允许任意有效 PAT 调用，用于身份
确认和客户端初始化；其余普通业务端点按上表检查 scope。
