# 去除 LangGraph/LangChain 依赖 + 统一持久化设计

> 状态：设计阶段，待持久化改造（P5/P6）完成后实施

---

## 背景

### 当前依赖程度

LangGraph/LangChain 在项目中的使用范围很小：

| 依赖 | 文件 | 用途 |
|------|------|------|
| `StateGraph`, `END` | `core/graph.py` | 图定义与路由 |
| `interrupt`, `StreamWriter` | `core/graph.py` | 权限确认中断 + 流式事件 |
| `Command` | `core/controller.py` | 恢复中断执行 |
| `AsyncSqliteSaver` | `core/graph.py` | Checkpoint 持久化 |
| `AIMessage` 等 | `models/llm.py` | 消息类型（包装后供 base.py 读取） |

实际 LLM 调用走 LiteLLM，状态管理用自定义 `AgentState`，事件系统用自定义 `StreamEvent`。LangGraph 本质上只提供了一层状态机壳子。

### 当前持久化问题：两套独立机制，各丢各的数据

| 层 | 存了什么 | 丢了什么 |
|---|---|---|
| **Messages 表** | 用户问题 + 最终 response 文本 | 中间执行过程全丢 |
| **LangGraph checkpoint** (304MB) | 每个节点的全量 state BLOB | 执行完就没用，无法查询 |
| **SSE 事件流** | 内存队列，30s TTL | 连接断了就没了 |
| **execution_metrics** | 内存中累积，只发前端一次 | 服务端不持久化 |

前端刷新页面只能看到一问一答，整个执行过程（agent 调用链、工具参数与结果、token 用量、耗时）全部丢失。

### 单工具调用限制

当前架构下 agent 每轮只能调用一个工具：

- `pending_tool_call` 是单值字段（`Optional[Dict]`），不支持多个待执行工具
- `base.py:387` 解析工具调用后只取 `tool_calls[0]`，其余直接丢弃
- `tool_execution_node` 一次只处理一个工具，然后路由回 agent
- 通过 prompt 约束 agent "单轮只调用一个工具"来规避

当 LLM 想并行调多个工具时（如同时搜索+抓取），被迫拆成多轮串行调用，增加了延迟和 token 消耗。混合权限场景（部分工具需要确认，部分不需要）更是无法处理。

---

## 方案：完整事件流模型

### 核心思想

**执行过程本身就是数据，事件流就是真相源（event sourcing）。**

一张 `MessageEvent` 表记录执行过程中的所有事件，不截断、不丢弃。agent 的完整 response、tool 的完整 result、token 用量、reasoning — 全部作为事件数据持久化。任何时刻的执行状态都可以从事件序列重建。

一张表同时承担三个角色：

1. **历史记录** — 按 message_id 查所有事件，重建完整执行过程
2. **可观测性** — 按 event_type / agent / tool 聚合查询、统计分析
3. **interrupt/resume** — interrupt 是事件流中的一个状态，恢复时从事件流重建上下文

### 数据模型

```
Message (现有，扩展)
  ├── content              用户输入
  ├── response             最终回复（冗余，方便列表查询）
  ├── execution_id         执行标识（原 thread_id，改名）
  ├── metadata (JSON)      execution_metrics 汇总（冗余，方便统计）
  │
  └── MessageEvent (新表，append-only，不可变)
        ├── id (PK)        自增主键，天然有序，兼做 sequence
        ├── message_id     FK → Message
        ├── event_type     见下方事件类型清单
        ├── ref_event_id   可选，引用关联事件（nullable）
        │                  约束：partial unique index WHERE event_type='interrupt_resolved'
        ├── agent_name     产生事件的 agent（nullable）
        ├── data (JSON)    事件完整数据，不截断
        └── created_at     时间戳
```

### 事件类型清单

| event_type | 时机 | data 内容 | ref_event_id | 持久化 |
|---|---|---|---|---|
| `agent_start` | agent 开始执行 | `{agent}` | - | 写库 |
| `llm_chunk` | LLM 流式 token | `{token}` | - | **仅推 SSE，不写库** |
| `llm_complete` | 单轮 LLM 调用完成 | `{content, reasoning_content, token_usage}` | - | 写库 |
| `tool_start` | 工具开始执行 | `{tool, params}` | - | 写库 |
| `tool_complete` | 工具执行完成 | `{tool, result, success, duration_ms}` | → tool_start | 写库 |
| `agent_complete` | agent 执行结束 | `{agent, summary}` | - | 写库 |
| `interrupt_pending` | 需要用户确认 | `{tool, params, execution_context}` | - | 写库 |
| `interrupt_resolved` | 用户确认结果 | `{approved}` | → interrupt_pending | 写库 |
| `execution_complete` | 整个请求执行完成 | `{response, execution_metrics}` | - | 写库 |
| `error` | 执行异常 | `{error, phase, agent}` | - | 写库 |

`llm_chunk` 是唯一不写库的事件 — 高频逐 token 流式，写库无意义。其余所有事件完整持久化。

### Interrupt 设计

**关键简化：事件流完整 → interrupt state 极轻。**

因为事件流记录了所有 agent response 和 tool result，interrupt 不需要序列化整个 AgentState。恢复时从事件流重建上下文，`interrupt_pending` 只存最小的执行位置：

```
#1   agent_start          {agent: "lead_agent"}
#2   llm_complete         {content: "我需要抓取...", reasoning_content: "...", token_usage: {...}}
#3   tool_start           {tool: "web_fetch", params: {url: "..."}}
#4   interrupt_pending    {tool: "web_fetch", params: {url: "..."}, phase: "TOOL_EXECUTING", current_agent: "lead_agent"}
     ← 暂停，等用户确认（interrupt_pending.data 只有几百字节）
#5   interrupt_resolved   {ref_event_id: 4, approved: true}
     ← 恢复：从 #1-#4 重建 agent memories / tool interactions，继续执行
#6   tool_complete        {ref_event_id: 3, tool: "web_fetch", result: {...}, success: true, duration_ms: 1200}
#7   llm_complete         {content: "根据抓取结果...", token_usage: {...}}
#8   agent_complete       {agent: "lead_agent"}
#9   execution_complete   {response: "...", execution_metrics: {...}}
```

**幂等性：append-only + partial unique index。**

```sql
-- 插入 interrupt_resolved，校验 ref 目标是同一 message 的 interrupt_pending
INSERT INTO message_events (message_id, event_type, ref_event_id, data)
SELECT :msg_id, 'interrupt_resolved', id, :resolve_data
FROM message_events
WHERE id = :pending_event_id
  AND message_id = :msg_id
  AND event_type = 'interrupt_pending'
-- SELECT 为空 → 目标不存在或类型不匹配，插入 0 行
-- partial unique 冲突 → 已处理过，插入失败
```

**Resume 并发控制：** `interrupt_resolved` 插入成功后，由 API 层启动执行 coroutine。`TaskManager` 通过 `execution_id` 去重 — 已有运行中任务则拒绝重复提交，返回 `409 Conflict`。

### 关键设计约束

**Append-only，不可变。** 事件一旦写入不做 UPDATE/DELETE。状态通过事件序列推导。

**每事件独立 commit。** 写入后立即提交（与现有 Repository 模式一致）。进程崩溃时已写入的事件保留，恢复时从最后一条事件继续。

**先写库，再推流。** 持久化成功获得 `event_id` 后再推 SSE 队列。前端收到的每个事件都携带 `event_id`，支持断线重连后从指定位置重放。

**data 不截断。** 事件存完整数据（完整 agent response、完整 tool result）。这是与 LangGraph checkpoint 的本质区别：checkpoint 每节点存全量 state 快照导致膨胀（304MB），事件流是增量追加，每条只存该事件自身的数据。一个请求的全部事件通常 ~20-50KB。

### 执行流程

```
请求进入
  → 创建 Message 记录（生成 execution_id）
  → 启动执行循环 (async coroutine, 由 TaskManager 管理)
      → while phase != COMPLETED:
          → 执行 agent（单轮 LLM 调用）
              → 写 agent_start 事件
              → LLM 流式调用：llm_chunk 仅推 SSE
              → LLM 完成：写 llm_complete 事件（含完整 response + token_usage）
          → 解析工具调用列表，串行执行每个工具：
              → 写 tool_start 事件
              → 检查权限：
                  ├── AUTO → 直接执行
                  └── CONFIRM → 写 interrupt_pending + 暂停等待
                      → 用户确认 → 插入 interrupt_resolved → 从事件流重建上下文 → 继续
              → 写 tool_complete 事件（含完整 result）
          → 写 agent_complete 事件
          → 路由：根据 phase 决定继续/切换 agent/结束
      → 写 execution_complete 事件（含汇总 metrics）
      → 更新 Message.response 和 Message.metadata（冗余写入，方便查询）
```

### 替代 LangGraph 的实现

| LangGraph 功能 | 替代方案 |
|---|---|
| `StateGraph` + 路由 | `while phase != COMPLETED` + phase switch（`ExecutionPhase` enum 已存在） |
| `interrupt()` / `Command(resume=)` | `interrupt_pending` 事件 + `interrupt_resolved` 恢复，从事件流重建上下文 |
| `StreamWriter` | 自定义 callback / async generator，复用现有 `StreamManager` |
| `AsyncSqliteSaver` (checkpoint) | 不需要 checkpoint，事件流本身就是持久化的执行历史 |
| `langchain_core.messages` | `llm.py` 的 `invoke`/`ainvoke` 改为返回 `LLMResponse`（已有 dataclass），删除 `to_langchain_message()` 中间层 |

### 多工具调用支持

`pending_tool_call` 从单值变为列表，**默认串行执行**，遇到 CONFIRM 权限正常 interrupt：

```
agent 单轮 LLM 调用
  → 解析出 [tool_a, tool_b(CONFIRM), tool_c]
  → 串行执行：
      1. tool_a (AUTO) → 直接执行
      2. tool_b (CONFIRM) → interrupt，等待用户确认后执行
      3. tool_c (AUTO) → 直接执行
  → 所有工具结果合并，注入下一轮 agent context
  → 一轮完成，而非三轮 LLM 往返
```

### 收益

- **完整历史**：加载消息时附带完整事件链，展示 agent response、tool 调用与结果、执行过程
- **可观测性**：结构化事件日志，可查询、可统计（token 消耗、工具成功率、响应耗时）
- **轻量 interrupt**：事件流完整 → interrupt state 只需执行位置，不再序列化整个 AgentState
- **统一持久化**：所有数据在同一个 DB，每事件独立 commit
- **依赖极简**：技术栈收敛为 FastAPI + LiteLLM + SQLAlchemy
- **多工具支持**：agent 单轮可调多个工具，串行执行，interrupt 自然嵌入

---

## 命名变更

| 现有 | 改为 | 原因 |
|------|------|------|
| `thread_id` | `execution_id` | 去掉 LangGraph 后不再有 "thread" 概念，实际语义是一次执行的标识 |

影响范围：`Message.thread_id` 字段、API schema (`chat.py`)、前端 SSE 连接 URL、controller 参数。内部项目同步发版，直接改名，不需要兼容窗口。

---

## 旧数据处理

不做迁移。`langgraph.db`（checkpoint 数据）直接删除，旧会话历史消息（Messages 表）保留但不补充事件数据。存量未完成的 interrupt 会话（依赖旧 checkpoint）自然失效。

---

## 实施顺序

1. **先完成持久化改造（P5/P6）** — 稳定应用数据层
2. **实现 MessageEvent 表 + 写入逻辑** — 在现有 LangGraph 基础上先加事件持久化
3. **替换执行引擎** — 用自己的 async 循环替代 StateGraph，interrupt 改为事件流模式
4. **改造 LLM 接口** — `llm.py` 返回 `LLMResponse`，删除 `AIMessage` 依赖
5. **移除 LangGraph/LangChain 依赖** — 清理代码和 requirements，删除 `langgraph.db`
6. **前端适配** — 历史消息加载事件链，展示执行过程
