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

一张 `MessageEvent` 表记录执行过程中每一步的完整结果。agent 的完整 response、tool 的完整 result、token 用量、reasoning — 全部作为事件数据持久化。任何时刻的执行状态都可以从事件序列重建。

注意：这里的"完整"指每个逻辑步骤的最终结果，而非传输过程的中间态。例如 `llm_complete` 记录完整 response，但逐 token 的 `llm_chunk`（仅为流式传输的拆包）不入库。

一张表同时承担两个角色：

1. **历史记录** — 按 message_id 查所有事件，重建完整执行过程
2. **可观测性** — 按 event_type / agent / tool 聚合查询、统计分析

### 数据模型

```
Message (现有，扩展)
  ├── content              用户输入
  ├── response             最终回复（冗余，方便列表查询）
  ├── summary (nullable)   compaction 后的摘要（优先用于构建 LLM context）
  ├── execution_id         执行标识（原 thread_id，改名）
  ├── metadata (JSON)      执行级状态：always_allowed_tools, execution_metrics 汇总
  │
  └── MessageEvent (新表，append-only，不可变)
        ├── id (PK)        自增主键，天然有序，兼做 sequence
        ├── message_id     FK → Message
        ├── event_type     见下方事件类型清单
        ├── agent_name     产生事件的 agent（nullable）
        ├── data (JSON)    事件完整数据，不截断
        └── created_at     时间戳
```

### 事件类型清单

| event_type | 时机 | data 内容 |
|---|---|---|
| `agent_start` | agent 开始执行 | `{agent}` |
| `llm_chunk` | LLM 流式 token | `{token}` |
| `llm_complete` | 单轮 LLM 调用完成 | `{content, reasoning_content, token_usage}` |
| `tool_start` | 工具开始执行 | `{tool, params}` |
| `tool_complete` | 工具执行完成 | `{tool, result, success, duration_ms}` |
| `agent_complete` | agent 执行结束 | `{agent, summary}` |
| `interrupt_pending` | 需要用户确认 | `{tool, params, execution_context}` |
| `interrupt_resolved` | 用户确认结果 | `{approved, always_allow}` |
| `execution_complete` | 整个请求执行完成 | `{response, execution_metrics, context_usage}` |
| `error` | 执行异常 | `{error, phase, agent}` |

所有事件执行期间在内存中累积，推 SSE 实时消费。`llm_chunk` 仅推 SSE 不入内存列表（流式传输拆包，无持久化价值）。其余事件在执行完成（或 error）时 batch write 到 DB。事件间关联通过序列顺序自然推导。

### Interrupt 设计

**Interrupt 是内存中的短暂暂停，不是持久化边界。**

执行 coroutine 遇到 CONFIRM 工具时 `await` 等待用户确认。整个过程在内存中完成，不涉及 DB 读写：

```
执行 loop（内存）
  → tool_b 需要 CONFIRM
  → 推 interrupt_pending 事件到 StreamManager（SSE 通知前端）
  → await asyncio.Event()              ← coroutine 暂停，等用户点确认
  → 用户点确认 → API 层 set event       ← coroutine 恢复
  → 更新内存 state.always_allowed_tools（如果 always_allow=true）
  → 继续执行 tool_b
```

**不持久化的理由：**
- Interrupt 是秒级到分钟级的短暂暂停，不是长期挂起
- 用户刷新页面 → 重连 SSE，StreamManager 有缓冲，仍能看到 interrupt 状态并确认
- 用户彻底离开 → coroutine 超时，当 error 处理并 flush 事件
- 服务器重启 → coroutine 丢失，用户重新提问

**幂等性：TaskManager 去重。** 用户连点两次 approve，coroutine 已经在跑（第一次 approve 后 event 已 set），第二次 API 请求被 TaskManager 通过 `execution_id` 拒绝，返回 `409 Conflict`。

**事件流示例：**

```
#1   agent_start          {agent: "lead_agent"}
#2   llm_complete         {content: "我需要抓取...", reasoning_content: "...", token_usage: {...}}
#3   tool_start           {tool: "web_fetch", params: {url: "..."}}
#4   interrupt_pending    {tool: "web_fetch", params: {url: "..."}}
     ← coroutine 暂停（内存 await）
#5   interrupt_resolved   {approved: true, always_allow: true}
     ← always_allow=true → 更新内存 state.always_allowed_tools
#6   tool_complete        {tool: "web_fetch", result: {...}, success: true, duration_ms: 1200}
#7   llm_complete         {content: "根据抓取结果...", token_usage: {...}}
#8   agent_complete       {agent: "lead_agent"}
#9   execution_complete   {response: "...", execution_metrics: {...}}
     ← 此时全部事件 batch write 到 DB
```

### 关键设计约束

**执行期间全内存。** Loop 运行时，状态（phase、current_agent、always_allowed_tools 等）和事件列表都在内存中。Loop 不读 DB，不写 DB，直到遇到持久化边界。

**两个持久化边界。** 只在以下时刻将内存中的事件列表 batch write 到 MessageEvent 表：
1. `execution_complete` — 成功，全量 flush
2. `error`（含 interrupt 超时）— 失败，flush 已有事件 + 错误信息

**SSE 与持久化分离。** 事件生成后走两条独立路径：推给 StreamManager（SSE 实时消费 + 断线重连缓冲）；追加到内存事件列表（等边界 flush 到 DB）。两者互不依赖。

**Append-only，不可变。** 事件写入 DB 后不做 UPDATE/DELETE。历史状态通过事件序列推导。

**data 存完整结果。** 事件如实记录每步的完整数据（agent response、tool result）。与 LangGraph checkpoint 的本质区别：checkpoint 每节点存全量 state 快照导致膨胀（304MB），事件流是增量追加，每条只存该事件自身的数据。

体积评估：一个普通请求 ~20-50KB。工具返回大数据（如 `web_fetch` 单 URL 最多 20K 字符）时单条事件可能较大，但这与工具返回给 agent 的数据量一致 — 数据体积应在工具层治理（调整 `max_content_length`、限制 `url_list` 数量），而非在事件层截断，否则会丢失 agent 实际接收的上下文，影响调试和重放。

### 执行流程

```
请求进入
  → 创建 Message 记录（生成 execution_id）
  → 启动执行循环 (async coroutine, 由 TaskManager 管理)
      state = 内存状态 (phase, current_agent, always_allowed_tools, ...)
      events = 内存事件列表

      → while phase != COMPLETED:
          → context_manager.build(state)          ← 全内存，读 state 不读 DB
          → agent LLM 调用（流式）
              → llm_chunk 推 StreamManager（SSE）
              → llm_complete 追加到 events
          → 解析工具调用列表，串行执行每个工具：
              → tool_start / tool_complete 追加到 events
              → 检查权限（读内存 always_allowed_tools）：
                  ├── AUTO → 直接执行
                  └── CONFIRM → 追加 interrupt_pending → 推 SSE → await（内存暂停）
                      → 用户确认 → 追加 interrupt_resolved → 继续
          → 路由：根据 phase 决定继续/切换 agent/结束

      → batch_write(events)                       ← 执行完成，全量 flush
      → 更新 Message.response 和 Message.metadata
```

### 执行引擎设计方向

参考 [Pi coding agent](https://github.com/badlogic/pi-mono) 的极简架构：**简单 while loop，唯一的抽象是 context 构建**，不搞 middleware 框架。

Pi 的核心设计：while loop 循环直到 LLM 不再调用工具为止，扩展点通过 config 上的回调注入（`transformContext`、`convertToLlm`、`getSteeringMessages`）。没有状态机、没有图、没有 middleware 链、没有 max-step 限制。

#### 唯一的抽象：`ContextManager.build()`

Loop 中只有一个真正需要抽象的扩展点 — **context 构建**。每轮 LLM 调用前，ContextManager 负责：

- 构建 system prompt（注入 artifact 清单）
- 注入对话历史（优先使用 `Message.summary`，无则用原文）
- 截断当前轮 tool interactions（保留最近 N 条）
- 追踪 context usage（token 计数、使用率）
- 触发跨轮 compaction（见 Compaction 设计）

```python
# 伪代码：执行循环
async def execute_loop(state, context_manager, emit):
    while state.phase != COMPLETED:
        # 唯一的抽象点
        context = context_manager.build(state)

        # 以下全是 loop 本体的固定逻辑，不是 hook
        response = await agent.call_llm(context.messages)  # 内含 retry
        tool_calls = parse_tool_calls(response)

        for tool in tool_calls:                   # 串行执行
            if needs_confirm(tool):               # 权限检查
                emit(interrupt_pending)            # 推 SSE（不写库）
                await wait_for_user()              # 内存暂停
            result = await execute(tool)

        route(state, response)                    # phase switch

    emit(execution_complete, context.usage)       # context_usage 随 metrics 写入
```

#### 不需要抽象为 hook 的部分

| 关注点 | 为什么不是 hook | 实际位置 |
|--------|----------------|----------|
| **Retry** | 基础设施代码，每个 agent 同样的 exponential backoff，没有定制需求 | `BaseAgent._call_llm_with_retry()` 内部逻辑 |
| **Tool limit** | 配置值 + loop 里一个 if 判断 | `agent.config.max_tool_rounds`，超限则注入 system message 提醒总结 |
| **Routing** | phase switch 就是 loop 本体，不是插件 | `merge_agent_response_to_state()` + `ExecutionPhase` enum |
| **Event emission** | 固定行为：追加内存列表 + 推 SSE，完成时 batch write | loop 内直接调用 `emit()` |

需要新增能力时（如 API call budget、cost tracking），直接在 loop 对应位置加代码即可，不需要中间件协议。

### 替代 LangGraph 的实现

| LangGraph 功能 | 替代方案 |
|---|---|
| `StateGraph` + 路由 | `while phase != COMPLETED` + phase switch（`ExecutionPhase` enum 已存在） |
| `interrupt()` / `Command(resume=)` | 内存 `asyncio.Event` await/set，不涉及 DB 持久化 |
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
- **轻量 interrupt**：内存 await/set，不涉及 DB，超时自动 error
- **统一持久化**：执行完成时 batch write 到同一个 DB
- **依赖极简**：技术栈收敛为 FastAPI + LiteLLM + SQLAlchemy
- **多工具支持**：agent 单轮可调多个工具，串行执行，interrupt 自然嵌入

---

## Compaction 设计

两层数据模型对应两层 compaction 机制：跨轮 compaction（Message 层）和轮内 truncation（tool interaction 层）。

### 跨轮 Compaction

**触发时机：** 用户新消息进来，ContextManager 构建 history 时发现 token 数超阈值，自动对最老的消息对做摘要。只在轮次边界触发，执行 loop 内部不做 compaction。

**形式：逐对摘要。** 每对 (User Q, AI A) 独立压缩为短版摘要，保持消息记录的独立性。不合并为单个 summary 块 — 合并会破坏消息结构，影响对话分支场景。

**摘要存储：`Message.summary` 字段。** 原始 `content` / `response` 保留不变，新增 `summary` 字段存摘要文本。ContextManager 构建 history 时优先读 `summary`，无则读原文。好处：原始数据不丢，UI 可选择展示原文或摘要。

**MessageEvent 清理：** Compaction 时删除已摘要消息的 MessageEvent 记录。此时执行已完成（不需要 resume），Message 保留摘要（不影响 history 构建），事件数据可安全清除。

```
新用户消息进来
  → context_manager.build() 构建 history
  → 计算 token count
  → 超阈值？→ 对最老的 N 对消息：
      1. LLM 生成逐对摘要 → 写入 Message.summary
      2. 删除对应 MessageEvent 记录
  → 重新 build（用 summary），直到 fit
  → 正常执行 loop
  → execution_complete metrics 带 context_usage
```

### 轮内 Truncation

执行 loop 中 agent 可能调多轮工具，`agent_memories.tool_interactions` 越来越长。不做 compaction（太复杂），直接 truncation：保留最近 N 条 tool interaction 在 context 里（现有 `compress_messages(preserve_recent=5)` 机制）。超出的交互已写到 MessageEvent，不影响持久化，只是不再注入 context。

### Context Usage

作为 `execution_complete` 事件 metrics 的一部分，不需要每轮推送：

```
context_usage: {
  total_tokens: 45000,
  capacity: 128000,
  usage_percent: 35.2,
  history_tokens: 12000,
  system_prompt_tokens: 3000,
  current_round_tokens: 30000,
  compacted_messages: 4
}
```

前端展示为消息级指标，如 "Context: 35% | 4 messages compacted"。不存在 100% 的情况 — 每轮开头自动 compact 到 fit。

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
4. **改造 TaskManager** — 增加 `execution_id` 去重（当前实现允许同 task_id 覆盖提交），resume 时拒绝重复执行
5. **改造 LLM 接口** — `llm.py` 返回 `LLMResponse`，删除 `AIMessage` 依赖
6. **移除 LangGraph/LangChain 依赖** — 清理代码和 requirements，删除 `langgraph.db`
7. **前端适配** — 历史消息加载事件链，展示执行过程
