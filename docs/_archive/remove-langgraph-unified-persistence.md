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

## 方案：统一事件模型

### 核心思想

**执行过程本身就是数据。** 把现在"即发即弃"的 SSE 事件流变成"写入即持久"的事件记录。一张 `MessageEvent` 表同时承担三个角色：

1. **历史记录** — 按 message_id 查所有事件，重建完整执行过程
2. **可观测性** — 按 event_type / agent / tool 聚合查询、统计分析
3. **interrupt/resume** — interrupt 就是事件流中的一个状态，不需要单独的 checkpoint 机制

### 数据模型

```
Message (现有，扩展 metadata)
  ├── content              用户输入
  ├── response             最终回复
  ├── execution_id         执行标识（原 thread_id，改名）
  ├── metadata (JSON)      execution_metrics 汇总
  │
  └── MessageEvent (新表，append-only)
        ├── id (PK)        自增主键，天然有序，兼做 sequence
        ├── message_id     FK → Message
        ├── event_type     agent_start / llm_complete / tool_start / tool_complete
        │                  / interrupt_pending / interrupt_resolved / ...
        ├── ref_event_id   可选，引用关联事件（nullable）
        │                  用途：interrupt_resolved 引用 interrupt_pending，
        │                       tool_complete 引用 tool_start 等
        │                  约束：partial unique index on (ref_event_id) WHERE event_type='interrupt_resolved'
        ├── agent_name     产生事件的 agent
        ├── data (JSON)    工具参数与结果、token 用量、reasoning 等
        └── created_at     时间戳
```

### 关键设计约束

**Durability：每事件独立 commit。** 事件是 append-only 的，每条写入后立即提交（与现有 Repository 模式一致）。不搞长事务 — 进程中途崩溃时已写入的事件仍然保留，恢复时可从最后一条事件继续。"统一持久化"指的是同一个 DB，不是同一个 transaction。

**Interrupt 幂等性：append-only + partial unique index。** 不修改 `interrupt_pending` 行，而是插入一条新的 `interrupt_resolved` 事件。`ref_event_id` 上的 partial unique index（仅对 `interrupt_resolved` 生效）防止重复 resolve，不影响其他事件类型复用该字段：

```
EventID  Type                 ref_event_id  Data
  42     interrupt_pending    NULL          {state: ..., tool: "web_fetch"}
  43     interrupt_resolved   42            {approved: true}   ← partial unique 防重
```

```sql
-- 校验 ref 目标确实是同一 message 的 interrupt_pending
INSERT INTO message_events (message_id, event_type, ref_event_id, data)
SELECT :msg_id, 'interrupt_resolved', id, :resolve_data
FROM message_events
WHERE id = :pending_event_id
  AND message_id = :msg_id
  AND event_type = 'interrupt_pending'
-- 1. SELECT 为空 → 目标不存在或类型不匹配，插入 0 行
-- 2. partial unique 冲突 → 已处理过，插入失败
-- 两种情况都天然幂等
```

崩溃恢复：只要存在引用某 `interrupt_pending` 的 `interrupt_resolved` 行，就知道应继续执行，不存在"CAS 成功但未执行"的卡死窗口。

**data 字段大小控制：** 工具返回结果（如 web_fetch 抓取内容）需做截断，设定单条 data 上限（如 64KB）。大结果存摘要 + 引用，避免 DB 膨胀。**例外：`interrupt_pending` 的 state 数据为恢复执行必需，不可截断。** 与 LangGraph checkpoint 的本质区别：checkpoint 每节点存全量 state 快照（~1MB+），MessageEvent 只存增量事件数据（~1-5KB）。

### 执行流程

```
请求进入
  → 创建 Message 记录（生成 execution_id）
  → 启动执行循环 (async coroutine, 由 TaskManager 管理)
      → while phase != COMPLETED:
          → 根据 phase 执行 agent / tool
          → 每个事件：
              1. 写 MessageEvent + commit（持久化，获得 event_id）
              2. 推 SSE 队列（携带 event_id，支持断线重放）
          → 遇到需要确认的工具：
              → 写 event_type=interrupt_pending（含序列化 state）+ commit
              → 返回前端等待确认
              → 用户确认后：插入 interrupt_resolved 事件（唯一约束保证幂等），读出 state 继续循环
      → 执行完成
          → 汇总 metrics 写入 Message.metadata
```

### 替代 LangGraph 的实现

| LangGraph 功能 | 替代方案 |
|---|---|
| `StateGraph` + 路由 | `while phase != COMPLETED` + phase switch（`ExecutionPhase` enum 已存在） |
| `interrupt()` / `Command(resume=)` | `interrupt_pending` 事件存 state，插入 `interrupt_resolved` 恢复（唯一约束保证幂等） |
| `StreamWriter` | 自定义 callback / async generator，复用现有 `StreamManager` |
| `AsyncSqliteSaver` (checkpoint) | 不需要通用 checkpoint，interrupt state 就是一条事件记录 |
| `langchain_core.messages` | `llm.py` 的 `invoke`/`ainvoke` 改为返回 `LLMResponse`（已有 dataclass），删除 `to_langchain_message()` 中间层，`base.py` 直接读 `.reasoning_content` / `.token_usage` |

### 多工具调用支持

自建执行循环后，`pending_tool_call` 从单值变为列表，支持 agent 单轮返回多个工具调用。**默认串行执行**，依次处理每个工具，遇到 CONFIRM 权限的工具正常 interrupt 等待确认：

```
agent 单轮 LLM 调用
  → 解析出 [tool_a, tool_b(CONFIRM), tool_c]
  → 串行执行：
      1. tool_a (AUTO) → 直接执行
      2. tool_b (CONFIRM) → interrupt，等待用户确认后执行
      3. tool_c (AUTO) → 直接执行
  → 所有工具结果合并，注入下一轮 agent context
  → agent 继续（一轮完成，而非三轮 LLM 往返）
```

- 减少不必要的 LLM 往返（省 token、降延迟）
- 串行保证工具执行顺序与 LLM 意图一致，interrupt 处理简单可靠
- 解除 prompt 中"单轮只能调一个工具"的人为限制

### 收益

- **前端历史回看**：加载消息时附带完整事件链，展示工具调用、agent 协作过程
- **可观测性**：结构化事件日志，可查询、可统计（token 消耗、工具成功率、响应耗时）
- **checkpoint 透明**：从 304MB 黑盒 BLOB → 按需存一条增量事件记录
- **统一持久化**：所有数据在同一个 DB，不存在跨库不一致（每事件独立 commit，非长事务）
- **依赖极简**：技术栈收敛为 FastAPI + LiteLLM + SQLAlchemy
- **多工具支持**：agent 单轮可调多个工具，串行执行，interrupt 自然嵌入

---

## 命名变更

| 现有 | 改为 | 原因 |
|------|------|------|
| `thread_id` | `execution_id` | 去掉 LangGraph 后不再有 "thread" 概念，实际语义是一次执行的标识 |

影响范围：`Message.thread_id` 字段、API schema (`chat.py`)、前端 SSE 连接 URL、controller 参数。

---

## 旧数据处理

不做迁移。`langgraph.db`（checkpoint 数据）直接删除，旧会话历史消息（Messages 表）保留但不补充事件数据。

---

## 实施顺序

1. **先完成持久化改造（P5/P6）** — 稳定应用数据层
2. **实现 MessageEvent 表 + 写入逻辑** — 在现有 LangGraph 基础上先加事件持久化
3. **替换执行引擎** — 用自己的 async 循环替代 StateGraph，interrupt 改写为事件记录
4. **改造 LLM 接口** — `llm.py` 返回 `LLMResponse`，删除 `AIMessage` 依赖
5. **移除 LangGraph/LangChain 依赖** — 清理代码和 requirements，删除 `langgraph.db`
6. **前端适配** — 历史消息加载事件链，展示执行过程
