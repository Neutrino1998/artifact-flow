# Tool 与 MCP 管理

本页解释管理员“工具管理”界面中的产品字段，不包含部署或实现细节。普通用户不能
通过 PAT 管理 Tool 定义或凭证。

## Tool 与 Tool unit

Tool 是 Model 最终调用的一项操作；Tool unit 是可见性、Agent/Skill/部门成员关系、
凭证和渐进式披露的管理边界。执行是否需要确认仍由具体 Tool 成员决定。

| 类型 | 适合什么 | 调用名称 |
|---|---|---|
| 单工具（1 个操作，singleton） | 一个独立 HTTP 操作，例如“查询股价” | Tool 全名等于 unit 名 |
| 工具集（多个相关操作，toolset） | 同一服务下共享可见性、凭证和成员关系的多个操作 | `<unit>__<member>` |
| MCP Server | 上游提供 MCP `streamable_http`，希望动态发现 Tool | `<unit>__<MCP tool>` |

不要因为“都属于同一个系统”就强行放进一个工具集。需要不同凭证、可见部门、
Agent/Skill 成员关系或 defer 策略时，应拆成不同 unit。

## Unit 字段

- **unit 名称**：全局唯一，不能包含 `__`；创建后不可修改。
- **类型**：创建后不可修改。需要换类型时应新建 unit，再迁移使用方。
- **可见性**：`public` 默认可用，`department` 需要部门授权；它不会自动把 unit 配给 Agent。
- **描述**：说明这组能力何时有用，避免只写系统名。
- **渐进式披露（defer）**：Agent 或 Skill 同时启用 `search_tools` 时，Model 先看到
  unit 索引，再按需加载完整参数；否则仍完整披露，不会变成不可用的死工具。

## HTTP Tool 成员

每个成员是一项独立操作，主要字段包括：

- **成员名称**：工具集内唯一；单工具会直接使用 unit 名。
- **执行权限**：`auto` 自动执行，`confirm` 每次先请求用户确认。
- **Endpoint 与 Method**：定义请求地址和 HTTP 方法；Endpoint 中的 `{id}` 引用同名参数。
- **请求头**：按键值配置固定 Header；敏感值应使用凭证占位符。
- **响应提取**：可选，用 JMESPath 从 JSON 响应中选择需要的部分。
- **超时**：限制单次上游调用等待时间。
- **保存响应为 Artifact**：将文本或二进制响应直接保存成对话 Artifact。二进制模式
  不能同时使用响应提取。

参数的 HTTP 位置由 Method 和 Endpoint 决定：

- Endpoint 中的 `{id}` 来自同名必填或带默认值的非 null 标量参数；
- `GET` / `DELETE` 的其余参数进入 query string；
- `POST` / `PUT` / `PATCH` 的其余参数进入 JSON body。

## 参数配置与高级 JSON Schema

JSON Schema Draft 2020-12 是 Tool 输入的唯一权威状态：同一份 Schema 既告诉 Model
应该传什么，也用于执行前校验。界面提供两种编辑方式，但不存在两份参数定义：

- **参数配置**：无损编辑常用顶层参数，包括类型、必填、说明、默认值、enum 和简单数组。
- **高级 JSON Schema**：编辑嵌套 object/array、`oneOf` / `anyOf`、条件和其他高级约束。

当现有 Schema 无法被参数配置无损表达时，界面只允许继续使用高级 JSON Schema。
这是为了避免切换表单时静默丢失约束，不代表 Schema 无效。

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "检索词"
    },
    "limit": {
      "type": "integer",
      "default": 20
    }
  },
  "required": ["query"],
  "additionalProperties": false
}
```

## 凭证

Endpoint 或请求头可使用 `{{TOOL_SECRET_*}}` 占位符。保存 unit 后，在详情页为检测到的
占位符填写凭证，再把 unit 挂载给 Agent。凭证值是 write-only：界面只显示是否已配置，
不会回显原值；需要更换时直接覆盖。不要把真实凭证写进名称、描述、参数 Schema 或对话。

Seeded unit 的定义和凭证状态在界面中只读；动态创建的 unit 才能在界面编辑和配置凭证。

## MCP Server

MCP Server 需要 URL、可选请求头、超时和默认执行权限。其 Tool 由上游动态发现，
默认执行权限应用于发现到的成员。保存或修改配置后先使用界面连接测试，再挂载给 Agent。

返回[产品使用指南](index.md)。
