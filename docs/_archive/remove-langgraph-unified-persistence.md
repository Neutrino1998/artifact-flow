# 去除 LangGraph/LangChain 依赖 + 统一持久化设计

> 状态：设计阶段，优先于 [optimization-plan.md](./optimization-plan.md) 持久化改造实施

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
  ├── content_summary (nullable)    用户问题的 compaction 摘要
  ├── response_summary (nullable)   AI 回答的 compaction 摘要
  ├── execution_id         执行标识（原 thread_id，改名）
  ├── metadata (JSON)      执行级状态：always_allowed_tools, execution_metrics 汇总, last_input_tokens
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
| `error` | 执行异常 | `{error, agent, context}` |

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
- 用户刷新页面 → 重连 SSE：
  - 迁移前（asyncio.Queue）：已消费事件丢失，用户需重新提问
  - 迁移后（Redis Streams，见 [optimization-plan.md](./optimization-plan.md) Phase 5.3）：支持 Last-Event-ID 重放，可恢复到 interrupt 状态继续确认
- 用户彻底离开 → coroutine 超时，当 error 处理并 flush 事件
- 服务器重启 → coroutine 丢失，用户重新提问

**幂等性：TaskManager 去重。** 用户连点两次 approve，coroutine 已经在跑（第一次 approve 后 event 已 set），第二次 API 请求被 TaskManager 通过 `execution_id` 拒绝，返回 `409 Conflict`。

**事件流示例（含 agent 切换）：**

```
#1   agent_start          {agent: "lead_agent"}
#2   llm_complete         {agent: "lead_agent", content: "需要搜索...", token_usage: {...}}
#3   tool_start           {agent: "lead_agent", tool: "call_subagent", params: {target: "search_agent", instruction: "搜索..."}}
     ← loop 识别 call_subagent → 切换 current_agent = search_agent
#4   agent_start          {agent: "search_agent"}
#5   llm_complete         {agent: "search_agent", content: "找到以下结果...", token_usage: {...}}
#6   tool_start           {agent: "search_agent", tool: "web_fetch", params: {url: "..."}}
#7   interrupt_pending    {tool: "web_fetch", params: {url: "..."}}
     ← coroutine 暂停（内存 await）
#8   interrupt_resolved   {approved: true, always_allow: true}
#9   tool_complete        {agent: "search_agent", tool: "web_fetch", result: {...}, duration_ms: 1200}
#10  llm_complete         {agent: "search_agent", content: "搜索结果汇总...", token_usage: {...}}
#11  agent_complete       {agent: "search_agent"}
     ← complete_agent() 检测 non-lead 完成 → 打包结果为 lead 的 tool_result，切回 lead
#12  tool_complete        {agent: "lead_agent", tool: "call_subagent", result: {data: "搜索结果汇总..."}}
#13  llm_complete         {agent: "lead_agent", content: "根据搜索结果...", token_usage: {...}}
#14  agent_complete       {agent: "lead_agent"}
#15  execution_complete   {response: "...", execution_metrics: {...}}
     ← 此时全部事件 batch write 到 DB
```

Context 构建时按 `agent_name` 过滤：lead 看到 #2, #3, #12, #13（call_subagent 是普通的 tool_start → tool_complete 对）；search 看到 #4, #5, #6, #9, #10（instruction 从 lead 的 #3 tool_start params 获取）。

### 关键设计约束

**执行期间全内存。** Loop 运行时，状态（completed、current_agent、always_allowed_tools 等）和事件列表都在内存中。Loop 不读 DB，不写 DB，直到遇到持久化边界。

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
      state = 内存状态 (completed, current_agent, always_allowed_tools, ...)
      events = 内存事件列表

      → while not completed:
          → agent_config = agents[state.current_agent]      ← 从预加载的 dict 取
          → drain message queue → state.queued_messages（非阻塞）
          → context_manager.build(state, agent_config)      ← 按 current_agent 过滤事件，拼接 system prompt，含 queued messages 合并
          → call_llm(agent_config, messages, emit)（流式，retry 在 llm.py）→ llm_chunk 推 SSE，llm_complete 追加到 events
          → 解析工具调用列表，串行执行每个工具：
              → call_subagent → emit tool_start + 切换 current_agent → break
              → tool_start / tool_complete 追加到 events
              → 检查权限（读内存 always_allowed_tools）：
                  ├── AUTO → 直接执行
                  └── CONFIRM → 追加 interrupt_pending → 推 SSE → await（内存暂停）
                      → 用户确认 → 追加 interrupt_resolved → 继续
          → complete_agent()：lead 无工具调用 → completed（跳出 loop，持久化）
                     sub 无工具调用 → 打包 tool_result 切回 lead，继续 loop

      → batch_write(events)                       ← 执行完成，全量 flush
      → 更新 Message.response 和 Message.metadata
```

### 执行引擎设计方向

参考 [Pi coding agent](https://github.com/badlogic/pi-mono) 的极简架构：**简单 while loop，唯一的抽象是 context 构建**，不搞 middleware 框架。

Pi 的核心设计：while loop 循环直到 LLM 不再调用工具为止，扩展点通过 config 上的回调注入（`transformContext`、`convertToLlm`、`getSteeringMessages`）。没有状态机、没有图、没有 middleware 链、没有 max-step 限制。

#### 唯一的抽象：`ContextManager.build()`

Loop 中只有一个真正需要抽象的扩展点 — **context 构建**。每轮 LLM 调用前，`context_manager.build(state, agent_config)` 负责：

- 拼接 system prompt — agent MD 的 role prompt + 根据 agent config 自动拼接（system info、task plan、artifact inventory、available agents、tool instructions），无模板变量
- 注入对话历史（优先使用 `content_summary`/`response_summary`，无则用原文）
- 合并 queued messages 到当前轮 user message（文本拼接）
- 截断当前轮 tool interactions（保留最近 N 条）
- 追踪 context usage（token 计数、使用率）
- 触发跨轮 compaction（仅在新用户消息进来的首次 build 时，loop 内后续 build 不触发。见 Compaction 设计）

```python
# 伪代码：执行循环（扁平 loop，lead/sub 共享同一个循环）
async def execute_loop(state, context_manager, emit):
    while not state.completed:
        agent_config = agent_registry.get(state.current_agent)  # 全局，启动时加载

        # drain 用户追加消息（非阻塞）
        queued = drain_queue(state.execution_id)
        if queued:
            state.queued_messages = queued

        # 唯一的抽象点（按 current_agent 过滤事件，含 queued messages 合并 + 渲染 system prompt）
        context = context_manager.build(state, agent_config)

        # 以下全是 loop 本体的固定逻辑，不是 hook
        response = await call_llm(agent_config, context.messages, emit)  # retry 在 llm.py
        tool_calls = parse_tool_calls(response)

        for tool in tool_calls:                   # 串行执行
            if tool.name == "call_subagent":      # 特殊路由：切换 agent
                state.current_agent = tool.params.target
                emit(tool_start, agent=lead, tool=call_subagent)
                break                             # 下一轮 iteration 由 sub 执行
            if needs_confirm(tool):               # 权限检查
                emit(interrupt_pending)            # 推 SSE（不写库）
                await wait_for_user()              # 内存暂停
            result = await execute(tool)

        if not tool_calls:                        # agent 无工具调用 → 完成
            complete_agent(state, response, emit)

    emit(execution_complete, context.usage)       # context_usage 随 metrics 写入
```

**`complete_agent()` — 仅在 agent 无工具调用时触发：**

| 当前 agent | 行为 |
|-----------|-----------|
| lead | **`state.completed = True`，loop 跳出** → 持久化 |
| sub | **打包 sub 的 response 为 lead 的 `call_subagent` tool_result event** → `state.current_agent = lead_agent` → 继续 loop |

- `call_subagent` 的切出在 for 循环里（`break`），切回在 `complete_agent()` 里 — 入口和出口分离但都在同一个 loop 内
- 一个 loop、一条事件流 — events 按 `agent_name` 过滤，`context_manager.build(state, agent_config)` 只为当前 agent 构建 context
- Interrupt/resume 天然正确：恢复时只看 `current_agent`，loop 从哪断从哪续

#### 不需要抽象为 hook 的部分

| 关注点 | 为什么不是 hook | 实际位置 |
|--------|----------------|----------|
| **Retry** | 基础设施代码，所有 agent 同样的 exponential backoff，没有定制需求 | `llm.call_with_retry()` 内部逻辑 |
| **Tool limit** | 配置值 + loop 里一个 if 判断 | `agent_config.max_tool_rounds`，超限则注入 system message 提醒总结 |
| **Agent completion** | 仅在无工具调用时触发，不是插件 | `complete_agent()`：lead → `completed = True`；sub → 打包 tool_result + 切回 lead |
| **Event emission** | 固定行为：追加内存列表 + 推 SSE，完成时 batch write | loop 内直接调用 `emit()` |

需要新增能力时（如 API call budget、cost tracking），直接在 loop 对应位置加代码即可，不需要中间件协议。

### Agent 定义：数据而非类

执行引擎简化后，Agent 不再需要类（`BaseAgent` / `LeadAgent` / `SearchAgent`）。Agent 的全部实质内容是：

1. **配置** — 模型、max_tool_rounds、允许的工具及权限
2. **系统提示词** — 身份、行为规则、输出格式

两者都是静态数据，用 MD 文件（YAML frontmatter + role prompt body）即可表达：

```markdown
---
model: deepseek/deepseek-chat
max_tool_rounds: 15
tools:
  web_search: auto
  web_fetch: confirm
  call_subagent: auto
  create_artifact: auto
  update_artifact: auto
---

你是 ArtifactFlow 的主控 agent...

（纯角色定义：身份、行为规则、输出格式。无模板变量。）
```

**`load_agent()` 返回纯数据结构（config + role prompt），不是类实例。** Loop 中调用 `call_llm(agent_config, messages, emit)` — retry 逻辑在 `llm.py` 中，与 agent 无关。

**System prompt 自动拼接：** MD 文件只包含角色定义（role prompt），`ContextManager.build()` 根据 agent config 自动拼接完整 system prompt：

```python
system_prompt = concat(
    agent_config.role_prompt,                                    # MD body，纯文本
    build_system_info(),                                         # 系统时间等
    build_task_plan(state),                                      # 当前任务计划（所有 agent）
    build_artifact_inventory(state)  if has_artifact_tools(agent_config),   # 有 artifact 工具才注入
    build_available_agents()         if has_subagent_tool(agent_config),    # 有 call_subagent 才注入
    generate_tool_instructions(agent_config.tools),              # 从 ToolRegistry 生成
)
```

拼接规则由 ContextManager 控制，agent MD 文件不需要知道。条件注入替代了原来 LeadAgent 和 SubAgent 的类继承差异。

**移除的内容：**

| 原有 | 处理方式 |
|------|---------|
| `BaseAgent` 类 | 删除。retry 移到 `llm.py`，streaming 由 `emit` callback 处理 |
| `LeadAgent` / `SearchAgent` / `CrawlAgent` 子类 | 各自变为一个 MD 文件 |
| `build_system_prompt()` | MD body 提供 role prompt，ContextManager 根据 agent config 自动拼接其余部分 |
| `format_final_response()` | 不需要。loop 跳出时直接取 `response.content` |
| `AgentToolkit` 绑定 | agent config 的 `tools` 字段声明，ContextManager 读取生成 prompt |

**用户自定义 agent：** 只需新建一个 MD 文件，在 lead agent 的 `call_subagent` 工具中即可调用。无需写 Python 代码。

### 替代 LangGraph 的实现

| LangGraph 功能 | 替代方案 |
|---|---|
| `StateGraph` + 路由 + `ExecutionPhase` 枚举 | 扁平 `while not completed` loop + `current_agent` 字符串。旧 5 值枚举（`LEAD_EXECUTING` / `SUBAGENT_EXECUTING` / `TOOL_EXECUTING` / `WAITING_PERMISSION` / `COMPLETED`）简化为一个布尔值，agent 切换靠 `current_agent`，其余是 loop 内顺序逻辑 |
| `interrupt()` / `Command(resume=)` | 内存 `asyncio.Event` await/set，不涉及 DB 持久化 |
| `StreamWriter` | 自定义 callback / async generator，复用现有 `StreamManager` |
| `AsyncSqliteSaver` (checkpoint) | 不需要 checkpoint，事件流本身就是持久化的执行历史 |
| `langchain_core.messages` | `llm.py` 的 `invoke`/`ainvoke` 改为返回 `LLMResponse`（已有 dataclass），删除 `to_langchain_message()` 中间层 |
| `BaseAgent` 类继承体系 | Agent 定义为 MD 文件（YAML frontmatter + role prompt），`load_agent()` 返回纯数据，retry 移到 `llm.py` |

### 执行中消息注入（Message Queue）

参考 [Pi coding agent](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/) 的 message queuing 机制：用户可以在执行过程中追加指令，loop 在 turn boundary 非阻塞 drain。

**数据流：** TaskManager 持有每个执行的 `asyncio.Queue` → 用户通过 API inject → loop 在下一轮 LLM 调用前 drain 到 `state.queued_messages` → `ContextManager.build()` 合并到 user message。

**合并方式：** 工具结果已经标准化为 XML 文本注入 user message，queued message 同理 — 直接作为文本拼接到当前 user message 末尾，不依赖任何 provider 特定的消息格式（如 Anthropic multi content block），确保通过 LiteLLM 接入各类模型时兼容：

```
<tool_result name="web_fetch" success="true">...</tool_result>

<user_message>重点关注性能方面</user_message>
```

这是 `ContextManager.build()` 内部的一步（构建当前轮 user message 时检查 `state.queued_messages`），不是独立的 hook。

**与 interrupt 的对比：**

| | Interrupt | Message Queue |
|--|-----------|---------------|
| **触发** | loop 主动暂停等用户 | 用户主动追加 |
| **阻塞** | 是（`await Event`） | 否（drain 检查） |
| **注入时机** | 工具执行前 | 下一轮 LLM 调用前 |
| **数据结构** | `asyncio.Event`（二值信号） | `asyncio.Queue`（消息通道） |

两者共存在 TaskManager，互不干扰。单 worker 下 `asyncio.Queue` 天然工作，多 worker 时换 Redis List，接口不变（见 [optimization-plan.md](./optimization-plan.md) Phase 5.5）。

### 多工具调用支持

新 loop 中 `parse_tool_calls(response)` 直接返回列表，**默认串行执行**，遇到 CONFIRM 权限正常 interrupt：

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
- **依赖极简**：技术栈收敛为 FastAPI + LiteLLM + SQLAlchemy，无 Agent 类继承体系
- **Agent 可配置**：Agent 定义为 MD 文件，用户可自定义 agent（模型、工具、提示词），无需写 Python 代码
- **多工具支持**：agent 单轮可调多个工具，串行执行，interrupt 自然嵌入
- **执行中交互**：message queue 支持用户在执行过程中追加指令，turn boundary 非阻塞注入

---

## Compaction 设计

两层数据模型对应两层 compaction 机制：跨轮 compaction（Message 层）和轮内 truncation（tool interaction 层）。

### 跨轮 Compaction

**触发时机：** 用户新消息进来，ContextManager 构建 history 时发现 token 数超阈值。只在轮次边界触发，执行 loop 内部不做 compaction。

**范围：** 保留最近 `preserve_recent_pairs`（可配置，默认 N）对不动，其余全部 compact。一次性处理，不逐对试探。

**形式：逐对摘要。** 每对 (User Q, AI A) 独立压缩为短版摘要，保持消息记录的独立性。不合并为单个 summary 块 — 合并会破坏消息结构，影响对话分支场景。已有摘要的消息对不会被二次摘要。

**摘要存储：`content_summary` + `response_summary` 字段。** 原始 `content` / `response` 保留不变，分别新增摘要字段。ContextManager 构建 history 时优先读摘要，无则读原文（`msg.content_summary or msg.content`、`msg.response_summary or msg.response`）。分两个字段是为了保持 user/assistant 角色交替结构。好处：原始数据不丢，UI 可选择展示原文或摘要。

**MessageEvent 保留：** Compaction 不删除 MessageEvent 记录。ContextManager 构建 history 只读 Message 层（summary 或原文），不读 MessageEvent。保留事件数据方便查看历史执行细节。

**阈值判断：** 不自己估算 token 数。每轮执行结束时，将最后一次 LLM 调用的 `prompt_tokens`（模型返回的精确值）存入 `Message.metadata.last_input_tokens`。下一轮新消息进来时读取此值，作为 context 大小的 baseline — 新一轮只会更大（多了一对 Q/A 历史），如果已接近红线就先 compact 再执行。

**渐进式实现：**

- **Phase 1（随 LangGraph 移除）**：超限时直接从 context 中移除最老消息对（不写 summary，不调 LLM）
- **Phase 2（后续优化）**：异步调用配置的模型生成逐对摘要，写入 `content_summary` + `response_summary`

```
轮次 N 执行完成
  → Message.metadata.last_input_tokens = prompt_tokens（模型返回的精确值）

轮次 N+1 新消息进来
  → 读上一条 Message.metadata.last_input_tokens
  → 超阈值？→ 保留最近 N 对（preserve_recent_pairs，可配置），其余全部 compact：
      Phase 1: 直接从 context 中移除（不生成摘要）
      Phase 2: LLM 逐对生成摘要 → 写入 content_summary + response_summary
  → 正常执行 loop
  → execution_complete metrics 带 context_usage
```

### 轮内 Truncation

执行 loop 中 agent 可能调多轮工具，tool interactions 越来越长。不做 compaction（太复杂），直接 truncation：保留最近 N 条 tool interaction 在 context 里（现有 `compress_messages(preserve_recent=5)` 机制）。超出的交互仍在内存事件列表中（执行完成时 batch write 到 MessageEvent），只是不再注入 LLM context。

### Context Usage

`last_input_tokens` 已存入 `Message.metadata`（用于 compaction 阈值判断），前端可直接用它展示 context 使用率：

```
usage_percent = last_input_tokens / model_context_limit
```

不需要细分（history、system prompt、当轮工具交互等），模型不返回这些拆分，自己估算没意义。

---

## 命名变更

| 现有 | 改为 | 原因 |
|------|------|------|
| `thread_id` | `execution_id` | 去掉 LangGraph 后不再有 "thread" 概念，实际语义是一次执行的标识 |

影响范围：`Message.thread_id` 字段、API schema (`chat.py`)、前端 SSE 连接 URL、controller 参数。内部项目同步发版，直接改名，不需要兼容窗口。

---

## 旧数据处理

不做迁移，全部清理。`langgraph.db`（checkpoint 数据）直接删除。Messages 表涉及字段重命名（`thread_id` → `execution_id`）和新增字段（`content_summary`、`response_summary`、`metadata.last_input_tokens`），旧数据结构不兼容，连同关联的 Conversations、Artifacts 等一起清空，重建 schema。内部项目无外部用户，一步到位。

---

## 部署假设：单 Worker

初始设计只考虑单 worker（一个 uvicorn 进程，一个 event loop）。多 worker 部署（gunicorn 多进程）属于后续优化范围（见 [optimization-plan.md](./optimization-plan.md) Phase 5.5）。

**单 worker 下的简化：**

| 组件 | 单 worker | 多 worker |
|------|-----------|-----------|
| **TaskManager interrupt** | `asyncio.Event` await/set，同进程内直接通信 | Redis pub/sub 跨进程通知 |
| **TaskManager message queue** | `asyncio.Queue` 每执行一个 | Redis List |
| **TaskManager 去重** | 内存 `dict` 查 `execution_id` | Redis 分布式锁 |
| **StreamManager** | `asyncio.Queue` 内存缓冲 | Redis Streams |
| **always_allowed_tools** | 内存 state 直接读写 | 不变（执行在单进程内完成） |

单 worker 意味着执行 coroutine 和 API 请求在同一个 event loop 中，`asyncio.Event` 天然工作，不需要跨进程协调。

---

## 与优化计划的交叉影响

> 以下 Phase 编号引用自 [optimization-plan.md](./optimization-plan.md)。

### Phase 5.2（Redis Checkpointer）→ 不再需要

LangGraph 移除后不再有 checkpoint 概念，Phase 5.2 的 `AsyncSqliteSaver → AsyncRedisSaver` 迁移变为无效工作。LangGraph 移除已调整为优先实施，Phase 5.2 直接跳过。

### Phase 5.3（StreamManager → Redis Streams）

Phase 5.3 迁移后，事件存储形成三层架构：

```
内存事件列表（执行期间累积）
  ↓ 实时推送
Redis Streams / StreamManager（SSE 传输 + 断线重连缓冲）
  ↓ 边界 flush
MessageEvent 表（永久历史）
```

- **迁移前（当前）**：用户刷新页面，`asyncio.Queue` 中已消费事件丢失。Interrupt 期间刷新 → 丢失 interrupt_pending 事件，但 coroutine 仍在等待，用户需重新发送消息。
- **迁移后**：Redis Streams 支持 `Last-Event-ID` 重放，刷新后可恢复到 interrupt 状态继续确认。

本设计不依赖 Phase 5.3 — 当前 asyncio.Queue 能工作，只是刷新体验较差。Phase 5.3 是增强，不是前置条件。

### Phase 5.5（TaskManager 多 Worker）

多 worker 下 TaskManager 需要：
- Redis 分布式锁做 `execution_id` 去重
- Redis pub/sub 做跨进程 interrupt 通知（API 请求可能落在 worker A，执行 coroutine 在 worker B）

当前设计的 `asyncio.Event` 方案在单 worker 下完备，多 worker 扩展时替换为 Redis 实现，接口不变。

---

## 实施顺序

LangGraph 移除优先于 [optimization-plan.md](./optimization-plan.md) 中的持久化改造，避免在即将废弃的 checkpoint 机制上做无用投入（Phase 5.2 Redis Checkpointer 直接跳过）。

1. **替换执行引擎** — 用 async while loop 替代 StateGraph，interrupt 改为内存 asyncio.Event
2. **改造 TaskManager** — 增加 `execution_id` 去重（当前实现允许同 task_id 覆盖提交），增加 interrupt Event 管理 + message queue
3. **改造 LLM 接口** — `llm.py` 返回 `LLMResponse` + `call_with_retry()`，删除 `AIMessage` 依赖
4. **Agent 类 → MD 文件** — `BaseAgent`/`LeadAgent`/`SearchAgent`/`CrawlAgent` 类删除，各自转为 MD 文件（YAML frontmatter + role prompt），新增 `load_agent()` 加载器
5. **移除 LangGraph/LangChain 依赖** — 清理代码和 requirements，删除 `langgraph.db`
6. **实现 MessageEvent 表 + 写入逻辑** — 事件持久化（此时执行引擎已是新 loop）
7. **继续 optimization-plan.md 中的持久化改造（跳过 Phase 5.2）** — Redis 迁移、StreamManager 升级等
8. **前端适配** — 历史消息加载事件链，展示执行过程
