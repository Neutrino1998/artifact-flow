# Tool 与 MCP 配置

一个 Tool 是一项可独立授权、可被模型调用的操作。外部集成优先使用声明式 HTTP Tool 或 MCP；只有需要应用内部逻辑时才编写 Python Builtin。

## HTTP Singleton

在 `config/tools/stock_price.md` 创建：

```markdown
---
name: stock_price
description: Query the latest price for a stock symbol.
type: http
visibility: public
defer: false
permission: auto
endpoint: https://api.example.com/price
method: GET
headers:
  Authorization: Bearer {{TOOL_SECRET_STOCK_API_KEY}}
timeout: 60
response_extract: data.price
parameters:
  - name: symbol
    type: string
    description: Stock ticker such as AAPL
    required: true
---

Use this tool for current stock prices.
```

主要字段：

| 字段 | 默认 | 说明 |
|---|---|---|
| `name` | — | 全局唯一名称，不能与 Builtin/Reserved 名冲突 |
| `type` | `http` | 当前 HTTP 定义只接受 `http` |
| `visibility` | `public` | `public` 或 `department` |
| `defer` | `false` | 为 `true` 时仅注入索引，模型通过 `search_tools` 按需展开 |
| `permission` | `confirm` | `auto` 或 `confirm`；这是执行权限唯一来源 |
| `endpoint` | — | 固定 URL，可包含参数路径模板和 Secret 占位符 |
| `method` | `GET` | `POST` / `PUT` / `PATCH` 使用 JSON body，其他方法使用 query string |
| `headers` | `{}` | 固定请求头，可引用 Secret |
| `timeout` | `60` | 单次上游请求超时，单位秒 |
| `response_extract` | — | 可选 JMESPath，如 `data.items[*].id` |
| `parameters` | `[]` | 模型可填写的参数 |
| `artifact_output` | — | 可选，把响应直接保存为文本或二进制 Artifact |

参数类型支持 `string`、`integer`、`number`、`boolean`、`json`。每个参数可声明 `description`、`required`、`default` 和 `enum`。

## Secret

HTTP Tool 和 MCP 只能引用 `TOOL_SECRET_` 前缀的占位符：

```yaml
headers:
  Authorization: Bearer {{TOOL_SECRET_CRM_TOKEN}}
```

然后在 `.env` 中提供：

```dotenv
TOOL_SECRET_CRM_TOKEN=replace-me
```

Reconcile 会把 seeded Secret 使用 `ARTIFACTFLOW_CREDENTIAL_KEY` 加密后写入 `tool_credentials`。缺少 Secret 时定义仍可入库，但调用会明确失败；非 `TOOL_SECRET_` 引用会在 reconcile 时被拒绝。

## Toolset

多个相关 endpoint 应组成一个 unit：

```text
config/tools/crm/
├── _set.md
├── search_customer.md
└── create_ticket.md
```

`_set.md` 保存 unit 级 `name`、`description`、`visibility` 和 `defer`。成员文件使用与 Singleton 相同的 HTTP 字段。

若 unit 名为 `crm`，成员 `search_customer` 的可调用全名是 `crm__search_customer`。Agent 和 Skill 都以整个 `crm` unit 为授权粒度。unit 名不能包含 `__`。

大型 Toolset 建议 `defer: true`，避免把所有 endpoint schema 注入每次模型调用。

## MCP Server

在 `config/mcp/internal_search.md` 创建：

```markdown
---
name: internal_search
description: Search the internal knowledge service.
type: mcp
transport: streamable_http
url: https://mcp.internal.example/mcp
headers:
  Authorization: Bearer {{TOOL_SECRET_MCP_TOKEN}}
timeout: 60
default_permission: confirm
visibility: department
defer: true
---

Use for internal knowledge queries.
```

当前只支持 `streamable_http`，不支持 stdio。Reconcile 只保存 Server unit；每个 Backend 进程在用户回合开始时执行 `tools/list` 并短期缓存发现结果。Server 不可达时本轮会显示明确错误，不会把历史缓存当成永久真相。

MCP 工具的可调用名称同样带 unit 前缀，例如 `internal_search__query`；权限默认取 `default_permission`。

## 响应保存为 Artifact

文本响应：

```yaml
artifact_output:
  enabled: true
  mode: text
  content_type: text/csv
  filename: report.csv
  title: Latest report
```

二进制响应：

```yaml
artifact_output:
  enabled: true
  mode: binary
  filename: export.pdf
```

Binary 模式不能与 `response_extract` 同时使用。未指定 MIME 或文件名时会尝试从响应头推导，并使用安全默认值兜底。

## 校验与生效

仓库提供可复制但不会被加载的模板：

- `config/tools/_example.md`
- `config/tools/_example_toolset/`

运行：

```bash
python scripts/reconcile_config.py --dry-run
```

确认名称、参数、Secret 引用、JMESPath 和 Agent 引用都合法后，再进入正常 reconcile 或生产 Release 流程。
