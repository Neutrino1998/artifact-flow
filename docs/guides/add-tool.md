# 添加新工具

> 两条路径：**声明式 HTTP 工具**（MD 配置，零代码，覆盖 80% 集成场景）与 **Python 自定义工具**（`src/tools/builtin/` 下写代码，适用于需要本地逻辑的场景）。

本指南聚焦操作步骤。工具系统的整体设计（XML 格式、权限模型、执行流水线）见 [架构文档 · 工具系统](../architecture/tools.md)。

## 路径选择

| 场景 | 推荐路径 |
|------|---------|
| 调用外部 REST API（查询、webhook、微服务） | **A · 声明式 HTTP 工具** |
| 需要本地文件操作、子进程、复杂状态 | **B · Python 自定义工具** |
| 需要访问数据库连接、运行时资源 | **B · Python 自定义工具** |

---

## 路径 A — 声明式 HTTP 工具（推荐）

在 `config/tools/` 创建 `.md` 文件，启动时自动加载。不写 Python。

### 三步流程

**1. 在 `config/tools/` 创建 `.md` 文件**

文件名自由命名，但以 `_` 开头的文件被忽略（`_example.md` 就是这样被跳过的）。

**2. 编写 YAML frontmatter**

```yaml
---
name: query_stock_price
description: "Query real-time stock price from exchange API"
type: http
permission: confirm
endpoint: "https://api.example.com/stock/price"
method: POST
headers:
  Authorization: "Bearer {{STOCK_API_KEY}}"
timeout: 30
response_extract: "$.data.price"
parameters:
  - name: symbol
    type: string
    description: "Stock ticker symbol, e.g. AAPL"
    required: true
  - name: market
    type: string
    description: "Market exchange"
    enum: [US, HK, SH]
    default: "US"
---

Query real-time stock price from the exchange API.
Use this when the user asks about current stock prices or market data.
```

MD body（frontmatter 之后的内容）会追加到 `description`，作为给 LLM 的扩展使用说明。

**3. 在 Agent frontmatter 中引用**

```yaml
# config/agents/finance_agent.md
tools:
  query_stock_price: confirm
```

重启服务生效。

### Frontmatter 字段参考

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `name` | string | 是 | — | 工具唯一标识；与 builtin 或保留名（`create_artifact` 等）冲突会启动失败 |
| `description` | string | 否 | `""` | 一句话说明，注入 tool_instruction 供 LLM 阅读 |
| `type` | string | 否 | `http` | 当前仅支持 `http` |
| `permission` | string | 否 | `confirm` | `auto` 或 `confirm`；自定义工具**默认 confirm 更安全** |
| `endpoint` | string | 是 | — | 完整 URL（含协议和路径） |
| `method` | string | 否 | `GET` | HTTP 方法；`POST/PUT/PATCH` 时参数走 JSON body，其他方法走 query string |
| `headers` | dict | 否 | `{}` | 请求头；值支持 `{{VAR}}` 环境变量模板 |
| `timeout` | int | 否 | `30` | 请求超时（秒） |
| `response_extract` | string | 否 | — | JSONPath 表达式，从响应中提取子字段 |
| `parameters` | list | 否 | `[]` | LLM 可传递的参数列表，详见下表 |

### Parameters 字段

每个 parameter 包含：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 参数名，也是 HTTP 请求中的字段名 |
| `type` | string | 是 | `string` / `integer` / `number` / `boolean` |
| `description` | string | 否 | 参数说明（供 LLM 理解用途） |
| `required` | bool | 否 | 默认 `true` |
| `default` | any | 否 | 默认值，LLM 未提供时填入 |
| `enum` | list | 否 | 枚举可选值；LLM 给出非 enum 值会被拒绝 |

### 密钥模板 `{{VAR}}`

在 `endpoint` 或 `headers` 中使用 `{{VAR_NAME}}`，运行时从环境变量（含 `.env`）读取并注入。**模板值不会出现在 LLM 上下文中**，LLM 看不到真实密钥。

```yaml
endpoint: "https://{{API_HOST}}/v1/query"
headers:
  Authorization: "Bearer {{STOCK_API_KEY}}"
  X-Region: "{{REGION}}"
```

未找到的变量保持原样（`{{VAR_NAME}}` 字面量）并记录警告，不阻断加载。

### JSONPath 响应提取

`response_extract` 是一个简易 JSONPath 表达式，支持：

- `$` — 根对象
- `$.key1.key2` — 嵌套访问
- `$.list[0]` / `$.data.items[2].name` — 数组索引

示例：API 返回 `{"code": 200, "data": {"price": 189.5}}`，`response_extract: "$.data.price"` 则工具返回 `189.5`。

超出支持范围（过滤器、通配符、递归下降）的表达式请在 agent role prompt 中引导 LLM 自行解析完整 JSON。

### 参考示例

`config/tools/_example.md` 是一个完整的可复制骨架。复制后去掉 `_` 前缀即被加载。

---

## 路径 B — Python 自定义工具

需要本地逻辑（文件、进程、DB 连接、复杂计算）时走这条路径。**当前需要修改项目代码**，不是纯配置加载。

### 五步流程

**1. 在 `src/tools/builtin/` 新建 Python 文件**

例如 `src/tools/builtin/my_tool.py`：

```python
from typing import List
from tools.base import BaseTool, ToolResult, ToolParameter, ToolPermission


class MyTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="Brief description for the LLM",
            permission=ToolPermission.CONFIRM,  # 默认 CONFIRM 更安全
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type="string",
                description="Input query",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type="integer",
                description="Max results",
                required=False,
                default=10,
            ),
        ]

    async def execute(self, **params) -> ToolResult:
        query = params["query"]
        limit = params["limit"]  # 默认值已由 _apply_defaults 填充

        try:
            # ... 实际逻辑 ...
            result_text = f"processed {query} with limit {limit}"
            return ToolResult(
                success=True,
                data=result_text,
                metadata={"query": query},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

**2. 实现 `get_parameters()` 与 `async execute()`**

参数类型强转、默认值填充、校验、执行异常包装都由 `BaseTool.__call__()` 统一处理 — 你的 `execute()` 只需关注业务逻辑。返回 `ToolResult(success, data, error, metadata)`。

**3. 在 `src/tools/builtin/__init__.py` 导出**

```python
from .my_tool import MyTool

__all__ = [
    # ... 现有导出 ...
    "MyTool",
]
```

**4. 在 `src/api/dependencies.py` 的 `_load_tools()` 中登记实例**

找到构造 `tools` 列表的位置，添加 `MyTool()`：

```python
tools = [
    WebSearchTool(),
    WebFetchTool(),
    CallSubagentTool(),
    MyTool(),   # 新增
]
```

**5. 在 Agent frontmatter 中引用**

```yaml
tools:
  my_tool: confirm
```

重启服务生效。

### BaseTool 关键约定

- **异步执行** — `execute()` 必须是 `async`，不要写阻塞调用（用 `asyncio.to_thread` 包同步库）
- **不抛异常** — 错误应返回 `ToolResult(success=False, error=...)`；抛出的异常会被 `__call__()` 兜底包装，但失去上下文
- **`data` 是字符串** — LLM 看到的是文本；结构化数据请序列化为 XML/JSON/表格等 LLM 友好格式
- **`metadata` 是 dict** — 前端 SSE 可拿到，但 LLM 看不到。Artifact 工具通过 `metadata["artifact_snapshot"]` 推送实时更新
- **参数类型** — `ToolParameter.type` 只支持 `string` / `integer` / `number` / `boolean`；复杂结构拆成多个参数或序列化为 string

### 权限级别选择

| 场景 | 建议 |
|------|------|
| 只读、幂等、无副作用（搜索、查询） | `AUTO` |
| 写入、花钱、发消息、改共享资源 | `CONFIRM` |
| 不确定 | `CONFIRM`（用户可在前端点"Always allow"加入白名单） |

### 保留工具名

以下名字是 Artifact 工具保留名，自定义工具不可同名：

```
create_artifact, update_artifact, rewrite_artifact, read_artifact
```

与 builtin 工具同名也会在启动时抛 `ValueError`（`build_tool_map` 中的冲突检测）。

---

## 通用注意事项

### agent 必须显式启用工具

新工具加载后，只有在 agent `tools` frontmatter 中列出才能被该 agent 调用。没有"全局默认可用"的概念。

### XML 调用示例自动生成

`BaseTool.to_xml_example()` 会根据 `ToolParameter` 自动生成 XML 调用示例，注入到 system prompt 供 LLM 参考。你不需要手写这部分。

### 测试建议

**路径 A（HTTP）：** 写一个 `tests/manual/your_tool_smoke.py`（**文件名不能以 `test_` 开头**，否则会被 pytest 自动收集），调用真实 endpoint 验证 response_extract 和认证。

**路径 B（Python）：** 在 `tests/` 下写单元测试，直接实例化工具类调用 `await tool(param1=...)`。`BaseTool.__call__` 的类型强转和校验可以顺带测到。

## 下一步

- [添加新 Agent](./add-agent.md) — 让新 Agent 使用新工具
- [架构 · 工具系统](../architecture/tools.md) — XML 格式、权限模型、执行流水线详解
