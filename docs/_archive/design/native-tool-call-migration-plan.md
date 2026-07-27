# Native Tool Call 迁移实施计划

> 状态：规划完成，待实施
>
> 创建日期：2026-07-19
>
> 最后更新：2026-07-27
>
> 相关文档：`docs/architecture/tools.md`、`docs/architecture/engine.md`、`docs/architecture/execution-lifecycle.md`、`docs/_archive/design/skill-system-implementation-plan.md`

## 本文档定位

本文档用于指导 ArtifactFlow 将模型侧工具调用协议从自定义 XML/CDATAs 一次性迁移为 OpenAI 风格的 native tool calls。

这里的“切片”仅指同一 feature branch 内可独立实现、测试和复核的施工检查点，不代表产品同时维护两套 runtime。分支在完成端到端切换并删除 XML runtime 前不可合并或发布。

本文档不是 native tool-call 协议的逐字段设计说明；实现中如发现字段或供应商差异，应将稳定结论回写到活动架构文档。

## 规模判断与切片结论

这是一个**中大型、强耦合迁移**，不是只替换 XML parser 的局部修改。主要耦合面包括：

- LLM 请求和流式响应：传入 tool schemas，组装 `tool_calls` 增量并解析 arguments。
- Engine 执行协议：以 `call_id`、name、arguments 驱动权限校验和工具执行。
- 事件与历史投影：从 XML 文本改为结构化 assistant/tool 消息。
- 渐进披露：把 per-agent skill/tool 披露状态纳入 effective tool set。
- Compaction：保留最近的真实 tool-call/result 结构闭合。
- 取消与异常：确保每个已生成的 native tool call 都有且仅有一个结束结果。
- 历史切换：在线预生成、停写后追加 compaction boundary，让所有既有 leaf 保留可继续上下文但不进入 legacy runtime。
- 工具命名与 schema：收紧为各私有推理端普遍可接受的 native function 约束。
- 工具目录与调用意图：保留 unit 级发现语义，并把现有单次调用理由迁移为保留参数。

建议在基线准备后按 5 个实现阶段推进，但只在一个分支完成最终切换。熟悉现有 engine 的工程师预计约需 **12–20 个工程日**，另需 active branch 语义摘要生成和私有推理端联调时间；供应商流式 chunk、chat template 差异、存量 conversation 数量和模型摘要吞吐是主要不确定项。其余 leaf 使用机械摘要，不消耗模型调用。

## 进度总览

| 阶段 | 内容 | 状态 | 合并/发布条件 |
|---|---|---|---|
| 0 | 建立迁移分支与基线 | 待开始 | 仅建立 branch，不发布 |
| 1 | Native schema、命名约束与流式 codec | 待开始 | branch 内测试通过 |
| 2 | 结构化事件、历史与 compaction 投影 | 待开始 | branch 内测试通过 |
| 3 | Per-agent progressive state | 待开始 | branch 内测试通过 |
| 4 | Engine 端到端切换与终态闭合 | 待开始 | native 主链路完整通过 |
| 5 | 历史切换、删除 XML runtime、联调收尾 | 待开始 | 全量验收后整体合并 |

依赖关系：阶段 1、2、3 可以在纯函数和 fixture 层交错推进；阶段 4 必须等待三者完成；阶段 5 必须等待 native 主链路稳定。

## 分支策略

- 从最新 `main` 创建 `codex/native-tool-calls`。
- 所有阶段在该分支内完成，可按阶段提交，便于回看和定位回归。
- 不引入协议 feature flag，不增加 per-model `tool_disclosure` 配置，不保留 XML/native 双 runtime。
- 不把阶段 1–4 的半成品单独合入 `main`；阶段 5 验收完成后整体合并。
- 不改写旧事件；迁移为所有现存 leaf/agent 追加 compaction boundary，新 runtime 不读取 boundary 之前的 XML 历史。

## 目标

迁移完成后，ArtifactFlow 只使用 native tool-call 协议与模型交互，同时保留现有系统真正有价值的上层能力：

- ArtifactFlow 生成并传入当前可见工具的 schema。
- 推理服务负责 chat template 渲染和 native tool-call 外层解析。
- ArtifactFlow 负责流式增量组装、arguments JSON 解码、参数校验、权限校验、执行和错误反馈。
- Skill/tool 渐进状态按 agent 存入对话分支 metadata，不依赖供应商私有 defer 协议。
- 所有当前可访问的 tool unit 继续以 unit description + 成员名形成轻量目录；只有 loaded 工具向 native `tools` 发送完整 schema。
- Skills、有效工具集、权限中断、事件溯源、compaction 和自愈反馈继续工作。
- Thinking 模型的 tool-calling assistant 以 `content + reasoning_content + tool_calls` 原样回传。
- 每个 native tool schema 都携带保留参数 `__reason`，延续现有用户可见的单次调用意图。
- 新协议中的每个 tool call 在历史和事件中结构闭合。

## 不在本次范围

- 不支持 XML/native 双 runtime 或自动回退。
- 不逐条把旧 XML 调用转换成 native tool calls，也不增加运行时翻译。
- 不保证从迁移前的任意非 leaf 内部消息节点重新分叉；迁移时存在的所有 leaf head 均可继续。
- 不依赖 vLLM/DashScope 等供应商私有的 `defer_loading`、`tool_reference` 扩展。
- 不引入宏大的“统一 canonical schema 平台”；只提供 `BaseTool` native schema 导出和 MCP raw schema 通路。
- 不把模型原始思维链当作调用理由，也不向用户暴露隐藏 reasoning；旧 `<reason>` 的产品语义由保留参数 `__reason` 承接。
- 不持久化每次请求的完整 native tools schema 快照；live schema 始终由当前 registry 生成，admin 不承诺历史 schema 的审计级复现。
- 不在首轮实现 provider capability matrix、strict mode 或自动供应商探测。
- 不新增 `tool_disclosure` 模型配置；现有静态 `defer` 即为唯一披露策略输入。

## 可观察结果

完成后应能直接观察到：

1. LLM 请求包含当前有效的 native `tools`，不再包含完整 XML 调用语法。
2. Engine 从 `tool_calls[].function.name/arguments` 执行工具，不再解析 assistant XML。
3. 历史中新的工具调用表现为携带 content/reasoning/tool calls 的 assistant 与对应的 `role=tool` 消息。
4. `read_skill`/`search_tools` 的效果只持久作用于调用它的 agent，并随对话分支继承。
5. Reminder 为所有当前可访问的 unit 展示 unit description、成员名和 loaded/deferred 状态，但不重复完整工具 schema。
6. 每次工具调用的 `__reason` 可用于权限确认、事件和前端展示，且不会传给业务工具。
7. 取消、超时、错误和 subagent 中止后不存在 orphan tool call。
8. Compaction 后最近一组真实 tool call/result 仍保持结构闭合且未被摘要替代。
9. 每次模型调用前都会在完整 tool-result 组之后追加一条 synthetic user reminder；图片也通过这条 user 消息传入。
10. 所有存量 leaf 经 semantic 或 mechanical boundary 后可继续，迁移前事件不再进入模型上下文。
11. XML parser、XML tool-call formatter 及其专属测试从 runtime 删除。

## 已确定的设计原则

### 1. Native 协议不等于把控制权交给推理服务

推理服务只负责把传入的 schema 渲染进模型模板，并把模型输出解析成 native tool-call 字段。ArtifactFlow 仍然拥有：

- 工具是否对当前 agent 可见；
- 工具 schema 的来源；
- JSON arguments 的解码与校验；
- 默认值、枚举、必填项和现有安全约束；
- 权限、确认、执行顺序、错误反馈和审计。

`function.arguments` 仍按 JSON 字符串处理，流式响应必须先按 call/index 组装完成，再 `json.loads` 和校验。

Native protocol 不认识 ArtifactFlow 的 unit，但 unit 仍是应用层的发现、权限和生命周期边界。每次请求对当前 agent 的 `EffectiveToolset` 形成两个投影：

- **轻量目录投影**：所有当前可访问的真实 tool unit 都进入 dynamic reminder，展示 unit description、成员 full name 和 `loaded`/`deferred` 状态。
- **Native schema 投影**：所有 non-deferred 工具，以及已由 `search_tools` 披露的 deferred 工具，进入请求的 `tools` 数组并携带完整 function schema。

目录不重复成员的完整 description、参数或 JSON Schema。这样 non-deferred unit 也可被模型发现和理解，但不会把同一份 schema 在 prompt 与 native 参数中发送两遍。`search_tools` 搜到已经 loaded 的工具时只说明“已可用”，不产生新的持久状态。

建议保持单一固定渲染形态，例如：

```text
<available_tool_units>
  <tool_unit name="A">
    Unit A description
    - A__tool_1 [deferred]
    - A__tool_2 [deferred]
  </tool_unit>
  <tool_unit name="B">
    Unit B description
    - B__tool_1 [loaded]
  </tool_unit>
</available_tool_units>
```

这里的 XML-like tag 只是无需解析的 prompt markup，不是继续保留 XML tool-call runtime。

### 2. Assistant envelope 必须完整回放 reasoning

当前 LLM 层、事件和前端已经能接收、持久化和展示 reasoning；迁移缺失的是把它投影回后续模型请求。新的 tool-calling assistant 消息必须保留：

```text
content + reasoning_content + tool_calls
```

- 即使 `content` 为空，只要存在 `tool_calls` 就必须生成 assistant 历史消息。
- `reasoning_content` 必须覆盖同一用户回合的多轮工具调用、下一用户回合以及 compaction carry；DeepSeek thinking 等端点会把缺失回放视为协议错误。
- reasoning 只回传模型并沿用现有受控 UI 展示，不作为 permission 文案，也不混入工具参数。
- ArtifactFlow 的 canonical message 字段继续使用现有 `reasoning_content`；所有 LLM 请求都经过 LiteLLM，由 LiteLLM adapter 负责不同供应商的 wire-field 映射。不要增加 `reasoning_replay_field` 一类 per-model 配置。
- 若目标端点出现可复现的字段兼容问题，优先升级 LiteLLM 或在唯一 LLM adapter 边界修复，并以端点 smoke 固化；不要把供应商分支扩散到 engine、history 或模型配置。
- `reasoning_content` 与用户可见的单次工具调用理由是两种数据。前者按 assistant envelope 回放；后者由第 8 节的 `__reason` 承接。
- Token usage 正常路径以推理端经 LiteLLM 返回的 `prompt_tokens`、`completion_tokens`、`total_tokens` 为准；reasoning 是 generated completion 的组成部分，不能把可选的 `reasoning_tokens` breakdown 再加到 `completion_tokens` 上。
- 推理端不返回可用 usage 时，本地 fallback 必须估算完整 assistant output envelope：`reasoning_content + content + canonical tool_calls`，不能继续只计算 content。该值明确标记为 estimate，只用于记账、上下文水位和 compaction 的 best-effort 判断。

### 3. Progressive state 是分支内、按 agent 隔离的持久状态

不通过遍历历史事件重新推导 skill/tool 披露状态。Controller 从父消息 metadata 恢复一个与 engine state 同形的 map，并在本轮结束时写入新消息：

```yaml
agent_progressive_state:
  lead_agent:
    active_skills: [artifact_workflow]
    disclosed_tools: [git__get_commit]
  research_agent:
    active_skills: []
    disclosed_tools: [web__fetch]
```

- `read_skill` 成功后只更新调用 agent 的 `active_skills`，并只激活该 agent 的 `EffectiveToolset` grants。
- `search_tools` 命中 deferred 工具后只更新调用 agent 的 `disclosed_tools`；命中已 loaded 工具不写状态。
- 用户 UI 发起的 skill 激活没有 agent selector，明确作用于 `lead_agent`。
- Lead 若希望 subagent 使用某项 skill 或 deferred tool，应在指令中要求 subagent 自行 read/search；状态不跨 agent 隐式传播。
- 状态随父消息自然继承到下一回合和分支，同一轮内先 read/search 后立即可见；不在每个用户回合重置。
- 只持久化 slug/full name，不缓存 schema。当前 registry 与当前 agent 的 `EffectiveToolset` 始终是授权来源；已删除、改名或失去权限的陈旧状态自然成为 no-op，不增加清理或同步机制。

对当前 agent 的工具暴露统一计算为：

```text
current = agent_progressive_state[agent_name]
native_tools = non_deferred(current_effective_toolset)
             ∪ (deferred(current_effective_toolset) ∩ current.disclosed_tools)
tool_catalog = all_real_tool_units(current_effective_toolset)
deferred_names = deferred(current_effective_toolset) - current.disclosed_tools
```

`tool_catalog` 对每个 unit 展示 description、成员 full names，并按成员是否包含在 `native_tools` 标记 `loaded`/`deferred`；不包含当前 agent 无权访问的 unit/member。`search_tools` 只有在命中 deferred 工具时才修改 `disclosed_tools`。

ArtifactFlow 每次调用都从当前 registry 生成 OpenAI-compatible schema；推理侧负责模型专用 template/parser。完整 tools schema 快照不是协议正确性所需，本次不持久化，admin 也不承诺用历史 registry 精确复现当时请求。

### 4. 结构闭合发生在任何事件持久化之前

硬性不变量：每个已接受的 `LLM_COMPLETE.tool_calls[].id` 必须有且仅有一个 `TOOL_COMPLETE.call_id`。

保持现有生成边界：流式 tool-call delta 只在内存中按 call/index 累积，provider stream **正常结束**后才形成 accepted calls、写入 `LLM_COMPLETE.tool_calls` 并进入执行。不能仅因某一时刻 id/name/arguments 看似完整就提前接受。取消、超时或流式错误发生在正常结束前时，部分 delta 不持久化为 tool calls、不执行，也不制造 START/COMPLETE；已流出的普通 content/reasoning 可沿用现有 partial `LLM_COMPLETE` 语义保存。此次迁移不新增 raw-delta 事件或另一套 provisional 状态机。

实现唯一、幂等的 `close_open_native_calls(final_state, terminal_reason)`，所有事件持久化入口共用：

- 正常与 cooperative cancel 路径在 terminal event 决策前调用，并在 SSE 仍存活时实时发送合成事件。
- external `CancelledError`、late cancel、shutdown/lease fencing 的直写路径必须在 `_persist_events` 前调用，不能绕过闭合。
- `_persist_events` 只断言输入已闭合，作为结构守卫；不能再实现第二套修复逻辑。
- 已开始但未完成：追加 `success=false` COMPLETE。
- 尚未开始的同轮剩余调用：追加配对 START 和 `success=false` COMPLETE，以维持现有前端事件配对。
- 已完成的 call id 不做修改；覆盖 CANCELLED、TIMED_OUT、ERROR 以及 subagent 返回 `None` 的展开路径。
- 执行前取消说明“未运行”；执行中取消说明“已取消，副作用状态可能不确定”。

数据库和重放结构闭合是强保证；如果 transport 已断开，实时 SSE 只能 best-effort。

### 5. Compaction 携带完整的最近真实调用

保留现有“LLM 完成后、工具执行前”触发 compaction 的时机。

- 自动 compaction 继续使用最近一次已完成调用由推理端返回的 `input_tokens + output_tokens` 判断水位。这是明确的 best-effort 契约：不增加请求前 tokenizer 调用、context-window 探测或第二套 provider-specific 计数逻辑。
- 该水位不是下一次请求的精确长度；后续 tool results、reminder、tools schema 和注入消息可能继续增长。沿用现有“工具作者约束输出大小、超限响亮失败”的边界，不在本次迁移引入预测或强一致预算机制。

若最新 LLM 消息包含 tool calls：

- Compactor 输入排除该条最新 tool-calling `LLM_COMPLETE`，避免摘要重复它。
- `COMPACTION_SUMMARY` 标记需要 carry latest tool call。
- EventHistory 在摘要消息之后，原样投影最近 assistant 的 `content + reasoning_content + tool_calls`。
- 随后产生的真实 tool results 正常接在该 assistant 消息之后。

目标历史形态为：

```text
user:      compaction summary
assistant: original content + original reasoning_content + original tool_calls
tool:      real result bound to tool_call_id
```

不伪造用于摘要的 assistant/tool 调用对，也不压缩最近的真实工具结果。

### 6. 旧历史通过双层 compaction boundary 切换

迁移目标只是保留存量对话的主题和当前工作，使用户能从迁移时存在的任意 leaf 继续；明确放弃对迁移前真实模型请求的审计级重建。旧 `MessageEvent` 保持 append-only，不更新、不删除。

为控制模型调用量，迁移脚本生成两类互斥的 lead-agent boundary：

1. **Semantic summary**：只为每个 conversation 的当前 `active_branch` 调用既有 compaction agent/prompt，保留主题、当前工作、关键事实和下一步。
2. **Mechanical summary**：为所有 leaf 纯机械地沿分支路径读取 display-only 的 `Message.user_input` 与 `Message.response`，复制成角色明确的对话摘要；天然去掉中间 tool-call 过程。保留首个用户问题/标题和最近若干完整 user/assistant 对，超限时插入明确省略标记；单条过大时按固定规则截断。

每个 leaf 最终只追加一个有效 lead boundary：active branch 的 semantic summary 成功时使用 semantic；失败时回退 mechanical；其他 leaf 直接使用 mechanical。路径内出现过的 subagent 追加便宜、确定性的 reset boundary，不调用模型，防止 `fresh_start=false` 的 subagent 继续读到 legacy XML。迁移时不存在的任意非 leaf 内部节点不作可继续承诺。

生成和 apply 分离：

- **在线 generate**：服务仍可写入时扫描 `(conversation_id, leaf_message_id, is_active_branch, last_event_id, head_fingerprint)` manifest；机械摘要和 active-branch 语义摘要均可预生成，不写 `MessageEvent`。
- **Checkpoint/resume**：使用独立 SQLite checkpoint 记录 `migration_id`、conversation/leaf/agent、扫描时 active 状态、`last_event_id`、head fingerprint、summary source、status、attempts、summary hash、latency 和 error；支持 `--resume`，不把一次性迁移状态引入 runtime。
- **有界并发与 ETA**：语义摘要使用 `--concurrency N`，对 429/5xx 有界重试；持续报告 total/completed/success/failed/inflight、滚动吞吐和基于剩余任务数的 ETA。
- **停写 apply**：维护窗口停止新请求并等待 active executions 清空，做数据库快照后重新扫描 leaf/head。未变化结果直接复用；变化或新增 leaf 立即重做 mechanical，变化后的 active branch 可重跑 semantic，失败仍回退 mechanical。
- **幂等追加**：最终校验每个 leaf/agent 均有候选 boundary 后，按 `(migration_id, leaf_message_id, agent_name)` 幂等追加 `COMPACTION_START` 与成功 `COMPACTION_SUMMARY`。缺 boundary、head 再次变化或 apply 失败才阻止部署；semantic 失败本身不阻塞。

Boundary 成为新 EventHistory 的右向扫描终点；新 runtime 永远看不到此前 XML event，因此不需要 legacy XML parser、formatter 或历史分支。迁移程序可携带独立 legacy 读取逻辑，但 runtime 不得 import 或调用。Cutover 时从缺省空 `agent_progressive_state` 开始，模型需要时重新 read/search。旧事件继续供 UI 展示；Admin 对 boundary 之前的请求明确返回“不支持精确重建”。

### 7. Reminder 与多模态使用统一的 synthetic user 消息

`MessageEvent` 继续是持久化真相源；每次请求先由 EventHistory 重新投影 provider messages，再由 ContextManager 注入仅属于本次请求的上下文。不要另存一份 provider message history 并与事件同步。

ContextManager 不再修改最后一条历史消息。每次 LLM 请求都在完整 tool-result 组之后，无条件追加一条新的 synthetic `role=user` message；连续 user message 是允许且已有的系统行为，不增加“可合并时合并”的小分支。

- Reminder text 始终放在这条 synthetic user message，不进入 `role=tool` content。
- OpenAI-compatible 的 `role=tool` 采用文本结果。若工具返回图片，先输出同组所有绑定 call id 的文本 tool results，再把图片 blocks 与 reminder text 一并放入这条 synthetic user 多模态消息。
- 同一 assistant 消息连续调用三个图片工具时，先产生三个绑定 call id 的文本 tool results，再只追加一条 synthetic user message；其中三个图片 block 分别标注 `tool_call_id`、`artifact_id`、version 和 content type。图片 user message 不得插入多调用结果组中间。
- 若模型在后续 round 再次调用图片工具，则每个完整调用组各有自己的 carrier message，按真实轮次交错；新的 carrier 只携带该组图片，不复制所有旧 vision blocks。现有跨回合 vision placeholder 与 text-only 模型降级语义保持不变。
- Synthetic message 不成为新的 MessageEvent 历史事实；实际发送的 reminder 继续随 `AGENT_START` 持久化，以支持当前 messages 级 admin 观察。

### 8. 用保留参数承接单次工具调用理由

标准 native tool call 没有与 function name 平级的“调用理由”字段，但现有 `<reason>` 已证明对权限确认、前端展示和错误自愈有价值。Native schema exporter 为每个工具统一注入：

```json
{
  "__reason": {
    "type": "string",
    "description": "Brief user-visible reason for making this call."
  }
}
```

- `__reason` 在 schema 中列为 required，提升模型稳定生成的概率；runtime 对缺失或非字符串仍宽容，使用确定性 fallback，不因此拒绝本可执行的工具调用。
- Engine 在业务参数校验和 `execute()` 前提取并删除 `__reason`；工具自身若合法声明业务参数 `reason`，保持原样，不发生冲突。
- `__reason` 写入 `TOOL_START`、permission request/interrupt、可观察事件和前端展示；不写入工具实际参数，也不使用隐藏 reasoning 替代。
- Builtin/HTTP schema 与 MCP raw `input_schema` 都在统一 exporter 边界注入该属性，并正确更新 `required`；即使原 schema 为 `additionalProperties: false` 也必须合法。
- Registry/config/MCP 外部定义不得占用保留名 `__reason`；在各自写入或 discovery 边界校验，MCP 冲突项跳过并 warning。

这会为每个工具增加少量 schema token，但配合渐进披露仍可控，且保留了 ArtifactFlow 已打磨的 per-call explanation 能力。

### 9. 工具名按 native 约束从入口保证

最终 full name 统一约束为：

```text
^[A-Za-z0-9_-]{1,64}$
```

- `ToolMember.full_name` 收紧为 `String(64)`。
- Seed 和管理端动态写入共用同一 validator，校验最终 `<unit>__<member>`，而不是只校验片段。
- MCP discovery 遇到不合法的最终名称时跳过并 warning，不做截断、hash 或隐藏别名映射。
- 已存在的不兼容名称通过迁移前检查失败并由维护者显式重命名。

## 实施阶段

## 阶段 0：建立分支与回归基线

### 包含

- 从最新 `main` 创建 `codex/native-tool-calls`。
- 记录当前 engine、history、compaction、skills、permissions 和 cancellation 相关测试基线。
- 准备 DeepSeek thinking、DashScope/Qwen 和 raw `openai/` + vLLM 三类 LiteLLM smoke 配置；环境缺失者明确记录为部署前验收项。
- 盘点全部 conversation leaf、各 conversation 的 `active_branch` 及路径内 agent，定义 manifest/head fingerprint 和迁移规模报告。
- 定义 SQLite checkpoint schema、`--resume`、有界并发、重试、滚动吞吐和 ETA 的 CLI 契约。
- 明确 cutover 维护窗口、写入停止方式、数据库快照和迁移失败回滚流程。

### 不包含

- 不创建 runtime 开关。
- 不先改生产配置或部署默认值。

### 验收

- 分支干净建立，现有相关测试可重复通过。
- 明确最终覆盖 LiteLLM 下的 DeepSeek、DashScope 和至少一个 vLLM 类实际目标端点。
- 迁移脚本能稳定枚举全部 leaf/agent、标记 active branch，且 checkpoint/report 能发现遗漏、重复 boundary、head 变化和摘要失败。

## 阶段 1：Native schema、命名约束与流式 codec

### 包含

- 为 `BaseTool` 增加 OpenAI 风格 function schema 导出。
- Builtin/HTTP tool 从现有 `ToolParameter` 生成 schema；MCP tool 沿用 raw `input_schema`。
- 在统一 exporter 为所有工具 schema 注入 required `__reason`，并在各定义入口禁止业务 schema 占用该保留名。
- LLM 层支持传入 `tools`，并按 call index/id 组装流式 `tool_calls` delta。
- 输出结构至少保留 content、reasoning content、tool calls、finish reason 和 usage。
- 正常 usage 直接采用 LiteLLM/provider 返回值；保留可选 reasoning breakdown 仅用于观测，不参与总数相加。
- Provider usage 缺失时，fallback 对 `reasoning_content + content + canonical tool_calls` 的完整 assistant envelope 做 token estimate，并明确标记 `estimated=true`。
- 任意流式 tool-call delta 在 provider stream 正常结束前都只存在于内存 buffer；取消、超时或错误时不得进入可回放 `LLM_COMPLETE.tool_calls`。
- arguments 完成后进行 JSON 解码，再进入现有参数校验；解析/校验失败产生可供模型自愈的明确 tool error。
- 落实 64 字符工具名约束和入口校验。
- 每次请求从当前 registry 与 per-agent effective tool set 生成通用 OpenAI-compatible schema；不保存 schema 快照，不写 provider template 适配或 per-model reasoning field 配置。
- 保持 `reasoning_content` 为内部 canonical 字段并交由 LiteLLM 完成 provider wire mapping；为三类目标端点增加 tool-call + reasoning 连续回放 smoke。

### 不包含

- Engine 此时不切生产主链路。
- 不实现 XML fallback。

### 验收

- 单调用、多调用、分片 name/arguments、空 content、非法 JSON、重复/乱序 delta 以及流式中途取消均有单元测试；只有正常 stream terminal 才接受 tool calls。
- 所有生成的 schema 可被目标 OpenAI-compatible 接口接受，`additionalProperties: false` 场景仍合法包含 required `__reason`。
- 不合法工具名或保留参数冲突在配置/写入边界失败，MCP 动态发现按约定跳过并记录 warning。
- DeepSeek reasoning 必须回放且不返回 400；DashScope 与 raw vLLM 经 LiteLLM 使用同一 ArtifactFlow message shape，无 per-model 字段分支。
- Usage fixture 覆盖 provider 正常返回、仅 reasoning、reasoning + content + tool calls，以及 provider usage 缺失；fallback 不漏算 reasoning/tool-call payload，且不会重复累加 reasoning breakdown。

## 阶段 2：结构化事件、历史与 compaction 投影

### 包含

- `LLM_COMPLETE` 可持久化 tool calls、content、reasoning content、finish reason 和 usage。
- `TOOL_START/TOOL_COMPLETE` 增加并投影 `call_id`。
- EventHistory 将新事件投影为携带 `content + reasoning_content + tool_calls` 的 assistant，以及逐个绑定 `call_id` 的文本 `role=tool` result。
- 实现并测试 compaction summary 后完整 carry latest real assistant envelope 的逻辑。
- ContextManager 不再假定或修改最后一条消息；每次请求都在完整 tool-result 组之后追加独立 synthetic user reminder。
- 图片工具结果在同组全部文本 tool results 之后，通过同一条 synthetic user message 的带来源标签 image blocks 传入。

### 不包含

- 不伪造 compaction tool call。
- 不压缩最新工具结果。
- 不在 runtime 投影或解析 boundary 之前的 legacy XML events。

### 验收

- assistant 可同时包含 content 和 tool calls，也可 content 为空。
- Thinking 模型在同回合后续调用、下一用户回合和 compaction carry 中都收到原始 `reasoning_content`。
- 多 tool-call 的结果严格按 `tool_call_id` 绑定。
- compaction 前后历史语义等价，最近 tool call/result 不重复、不丢失、不 orphan。
- 连续 user 消息、最后一条为 tool、图片与 reminder 共存时均产生同一种追加结构。
- 单图片、图片+文本多工具、同组多图片、跨轮多图片、text-only 降级和 compaction 后图片历史均有 fixture；每个 carrier 只包含其所属调用组的图片。

## 阶段 3：Per-agent progressive state

### 包含

- 在 engine state 与 `Message.metadata` 中增加 `agent_progressive_state` map，按父消息恢复并随当前分支持久化。
- 把当前全局 `active_skills` 行为迁移为 per-agent：`read_skill` 只更新调用 agent 的状态与 `EffectiveToolset`；用户 UI 激活明确写入 `lead_agent`。
- `search_tools` 返回结构化 discovered full names，并只更新调用 agent 的 disclosed 状态。
- Effective tool set 按当前 agent 统一计算 native-visible schema 与全量轻量 unit catalog；catalog 标记成员 `loaded`/`deferred`。
- Dynamic reminder 为所有当前可访问的真实 tool unit 渲染 unit description 和成员名，不重复成员完整描述/参数；`search_tools` 命中 loaded 工具不修改状态。
- Active skill grants、disclosed names、工具删除或 agent 权限变化始终与当前 agent 的实时有效权限求交。
- 同一轮调用即时更新内存 state；事件成功持久化后，再把完整 map 写入新消息 metadata。
- 删除不再需要的模型级 disclosure 配置路径（若存在）。

### 不包含

- 不在每个用户回合重置 disclosed 状态。
- 不缓存 schema，不增加新的披露模式。
- 不通过遍历历史事件重建 progressive state，不跨 agent 自动传播。

### 验收

- Read/search 后同回合、下一回合和同一分支均只对调用 agent 生效。
- Deferred 与 non-deferred unit 都出现在轻量目录；未披露 deferred tool 不进入 native `tools`，non-deferred 与已披露 deferred tool 进入完整 schema。
- 搜索已 loaded 工具只返回“已可用”；搜索 deferred 工具才写入调用 agent 的 `disclosed_tools`。
- 披露不能越过 agent/skill 权限，也不能让已删除工具复活。
- 会话分支只继承父消息 metadata 中各 agent 自己的状态。
- Lead 的 read/search 不改变 subagent 状态，反向亦然；subagent 必须自行 read/search。

## 阶段 4：Engine 端到端切换与终态闭合

### 包含

- `_call_llm` 传入当前 native tools，并返回结构化 tool calls。
- Engine 以 name、arguments、call id 驱动现有权限确认、串行执行、subagent 调用和结果记录；业务参数校验前提取保留的 `__reason`。
- 移除 engine 对 assistant XML tool-call parser 的调用。
- 工具校验/权限/执行错误统一返回与真实 call id 绑定的失败 tool result，保留现有自愈语义。
- Permission UI、`TOOL_START` 和前端展示优先使用调用自己的 `__reason`；缺失或非字符串时使用确定性文案“模型请求调用 X”，不读取原始 reasoning，也不阻止执行。
- 加入唯一、幂等的 orphan closure helper，并让正常后处理、cooperative cancel、external `CancelledError`、late cancel、shutdown/lease fencing 和 subagent unwind 都在任何事件持久化前经过它。
- `_persist_events` 断言 accepted native calls 已闭合，但不生成修复事件。
- 对仍连接的 SSE 发送 closure 生成的配对事件；transport 已断开时仍保证数据库与后续 replay 闭合。

### 不包含

- 不保留旧 engine 执行路径。
- 不按供应商写多套 executor。

### 验收

- 无工具、单工具、同轮多工具、subagent、权限确认/拒绝、参数错误再自愈均通过端到端测试；同轮每个 call 保留自己的 `__reason`。
- 业务工具可继续合法使用普通 `reason` 参数；保留的 `__reason` 不进入 `ToolParameter` 校验或 `execute()`。
- 取消发生在执行前、工具执行中、工具之间、subagent 内，以及 timeout/error 时，所有 call id 均恰好一个 COMPLETE。
- External cancel 的直写持久化路径同样满足闭合；provider stream 未正常结束时，其 buffered 调用不进入闭合集合，也不被执行。
- Engine 新主链路不 import 或调用 XML tool-call parser。
- 新一轮历史可被目标推理端连续消费，不出现 role/order/template 错误。

## 阶段 5：历史切换、删除 XML runtime、联调收尾

### 包含

- 实现独立的一次性迁移程序：在线为每个 conversation 的扫描时 active branch 并发生成 semantic summary，同时为所有 leaf 生成纯机械 user/final-response summary。
- 为 leaf 路径内的 subagent 生成确定性 reset boundary，不逐个调用 compaction model。
- 使用 SQLite checkpoint 支持 `--resume`、幂等 task key、有界并发、429/5xx 重试、失败明细、rolling throughput 与 ETA；generate/report 阶段不写 `MessageEvent`。
- Apply 前停止写入、等待 active executions 清空并完成数据库快照；重新校验全部 leaf 和 head fingerprint，变化/新增项补算，active semantic 失败自动采用 mechanical fallback。
- 在维护窗口的 apply 阶段幂等追加 `COMPACTION_START`/`COMPACTION_SUMMARY` boundary；重复执行不得产生重复有效 boundary。
- Native 部署前验证所有 leaf/agent 已成功切换，任何遗漏、head 再变化或 apply 失败都阻止部署；单纯 semantic 失败不阻塞。
- Cutover 后初始化空的 `agent_progressive_state`；旧原始事件继续供 UI 查看，但 boundary 前 admin 精确请求重建明确标记为不支持。
- 将迁移程序所需的 legacy rendering 与 runtime 隔离；迁移完成并验证后删除 runtime 的 XML tool-call parser、formatter、调用语法 prompt 和专属测试。
- 更新 tools、engine、history、compaction、execution lifecycle 等活动架构文档。
- 更新模型配置示例，明确私有部署必须提供兼容的 native tool-call chat template/parser。
- 对目标私有端点执行 smoke：至少覆盖 DeepSeek thinking、DashScope/Qwen 与 raw `openai/` + vLLM；无法在开发环境执行者必须在部署前目标环境补齐。
- 增加关键诊断日志：schema 拒绝、流式组装失败、arguments 解析失败、orphan closure 触发；用户消息保持脱敏。
- 执行全量后端测试和相关前端测试。

### Cutover 顺序

1. 先完成并验证 native 分支、一次性迁移程序和目标端点 smoke，不部署新 runtime。
2. 服务在线期间扫描 manifest，以 checkpoint 并发生成 active semantic summaries 与全 leaf mechanical summaries；持续报告进度和 ETA。
3. 进入维护窗口，停止新写入、等待 active executions 清空并完成数据库快照。
4. 重扫全部 leaf/head；复用未变化结果，补算变化/新增项，并为 semantic 失败选择 mechanical fallback。
5. 全部 leaf/agent 均有最终候选后执行幂等 apply，校验 boundary 数量、agent 覆盖、summary hash 和 head 未再次变化。
6. 校验全部通过后部署 native runtime；失败时继续运行旧版本或从快照回滚，不启用双 runtime。

### 不包含

- 不以保留 XML 作为供应商不兼容时的降级方案。
- 不自动修补不兼容 chat template；部署检查应响亮失败并给出诊断。
- 不逐事件改写旧历史，不承诺 pre-cutover request/schema 审计级重建，也不保证从迁移前非 leaf 内部节点新建分支。

### 验收

- 所有扫描到的 leaf/agent 都有可验证的成功 boundary；semantic 失败稳定回退 mechanical，任一 boundary 覆盖或 apply 失败都不会进入 native 部署。
- Active leaf 优先从语义摘要继续当前工作，其他 leaf 至少从机械 user/final transcript 继续；新 runtime 不读取 boundary 之前的 XML event。
- Checkpoint 中断后可 resume，不重复模型调用或 boundary；在线 generate 期间发生的新写入能在停写复核中被发现和补算。
- 代码库中不再存在运行时 XML tool-call 协议路径；离线迁移代码不会被 runtime import。
- 全量测试通过，目标私有端点 smoke 通过。
- 活动文档只描述 native runtime；归档文档保留历史背景但不作为当前行为依据。
- 分支达到可整体合并状态。

## 总体验收矩阵

| 场景 | 必须验证的结果 |
|---|---|
| 无工具回复 | content/reasoning/usage 正常，历史无伪 tool message |
| 单/多工具调用 | name、arguments、call id 正确组装，每个 call 的 `__reason` 独立展示，串行执行语义不变 |
| Thinking 回放 | 同回合工具循环、下一用户回合及 compaction carry 都原样回传 assistant `reasoning_content`；DeepSeek/DashScope/vLLM 均经 LiteLLM 使用同一内部字段 |
| Token usage | Provider usage 为权威且 reasoning breakdown 不重复相加；无 usage 时完整 envelope fallback 覆盖 reasoning/content/tool calls 并标记 estimated |
| 流式中途取消 | 正常 stream terminal 前的任何 delta 都不进入 `LLM_COMPLETE.tool_calls`、不执行、也不制造 closure pair；已流出 content/reasoning 可按现状保存 |
| 非法 JSON/参数 | 不执行工具，返回绑定 call id 的可自愈错误 |
| 权限拒绝 | 不执行工具，模型收到明确失败结果并可继续 |
| Per-agent deferred search | 搜索前不暴露 schema；搜索后同轮/跨回合只对调用 agent 保持披露 |
| Unit 目录 | Deferred/non-deferred unit 均展示 description、成员名和 loaded/deferred 状态；无权工具不出现，完整 schema 不在 reminder 重复 |
| Per-agent skill activation | Skill 只激活调用 agent；UI 激活只作用 lead；effective set 始终实时求交 |
| 分支继承 | 新分支继承父 metadata 的 per-agent 状态，不通过事件扫描重建 |
| Synthetic reminder | 无论最后消息角色为何，每次请求都追加独立 user message；连续 user 不触发特殊合并 |
| 多模态结果 | 每组所有文本 tool results 先闭合，再追加一条含该组带来源标签图片与 reminder 的 user message；覆盖同组/跨轮多图片和 text-only 降级 |
| Compaction | 摘要后保留最新真实 assistant content/reasoning/tool calls 和未压缩 tool results |
| Cancel/timeout/error | cooperative、external、late cancel、shutdown/lease fencing 等持久化路径中的 accepted call id 均恰好一个完成结果 |
| Subagent 中止 | caller 的 `call_subagent` 调用被失败闭合 |
| 旧历史迁移 | Active leaf 优先 semantic、失败回退 mechanical，其他 leaf 使用 mechanical，subagent 使用 reset；全 leaf/agent 有 boundary，旧事件保持 append-only |
| 迁移并发恢复 | 在线 generate 可 checkpoint/resume 并报告 ETA；停写复核能发现 head 变化且 apply 幂等 |
| Admin 重建 | 新请求保持 messages 级观察；pre-cutover 精确重建明确不可用，不伪造历史 tools schema |
| 私有推理端 | chat template 接受 schema 与消息序列，流式 parser 输出稳定 |

## 风险与控制

| 风险 | 影响 | 控制方式 |
|---|---|---|
| LiteLLM/供应商流式 chunk 形态差异 | arguments 丢片或 call 归属错误 | codec fixture 覆盖实际响应；最终端点 smoke |
| 私有模型 chat template 不支持标准 tools | 请求失败或模型输出普通文本 | 部署前置检查并响亮失败；不引入 XML fallback |
| Thinking assistant 未回放或 LiteLLM provider mapping 漂移 | DeepSeek 等端点后续请求 400 | 完整 assistant envelope fixture + DeepSeek/DashScope/vLLM smoke；问题收敛在 LiteLLM/LLM adapter |
| Provider 缺失 usage 时 fallback 漏算 reasoning/tool calls | Context 水位偏低，compaction 触发过晚 | 对完整 assistant output envelope 做保守 estimate 并标记 estimated；不承诺请求前精确预测 |
| Semantic 摘要失败 | Active branch 当前工作丢失 | 自动回退已经生成的 mechanical summary；记录失败但不单独阻塞 cutover |
| 迁移漏掉 leaf/agent 或在线 head 变化 | 存量对话无法继续或新 runtime 看到 legacy XML | Manifest fingerprint、checkpoint、停写重扫、DB 快照、部署前全量 boundary 校验 |
| Progressive state 意外跨 agent 传播 | 扩大 schema/权限面并污染上下文 | metadata map 以 agent_name 为 key，read/search 仅更新调用者，授权实时求交 |
| Unit 目录重复完整 schema | 上下文浪费且 defer 语义模糊 | Reminder 只含 unit description、成员名和状态；完整 schema 只走 native `tools` |
| Compaction carry 重复/遗漏 | tool result orphan 或上下文膨胀 | 边界 fixture + 结构闭合不变量测试 |
| 外部取消绕过正常 dispatcher | 数据库留下 orphan，历史请求被 provider 拒绝 | 所有持久化前调用唯一 closure helper，`_persist_events` 只做 closed assertion |
| 未正常结束的流式 delta 被当成 accepted call | 产生无法重放或错误执行的调用 | 仅正常 provider stream terminal 才把内存 buffer 提升为 `LLM_COMPLETE.tool_calls` |
| Tool-role 图片被 provider 拒绝 | 多模态工具历史不可消费 | `role=tool` 仅文本，图片统一放在全部 results 后的 synthetic user message |
| MCP 外部名称不兼容 | schema 被推理端拒绝 | discovery 时校验并 warning 跳过 |
| 模型遗漏/错写 `__reason` | Permission UI 缺少调用意图或工具误收内部参数 | Schema required 但 runtime 宽容 fallback；业务校验/执行前统一剥离保留参数 |
| 外部 schema 占用 `__reason` | 覆盖业务参数或 exporter 生成非法 schema | 配置/写入/discovery 入口拒绝冲突；普通业务参数 `reason` 不受影响 |

## 开放项

以下不阻塞架构实现，但应在阶段 0 或阶段 5 明确：

- 最终私有部署 smoke matrix 中具体有哪些模型、vLLM 版本和 chat template。
- `__reason` 缺失时 Permission UI 的最终 fallback 文案；协议策略已经确定，不影响执行结构。
- 部署检查应放在启动自检还是首次模型调用失败路径，优先选择最小且可诊断的实现。
- Mechanical summary 的 token/字符上限、保留最近对数，以及 semantic `--concurrency` 默认值；算法和 fallback 顺序已经确定。

## 完成定义

只有同时满足以下条件，迁移才算完成：

- 唯一运行时协议为 native tool calls。
- Per-agent deferred disclosure、unit catalog、skills、`__reason`、permissions、subagents、reasoning replay、multimodal、compaction 和取消语义均已迁移并有回归测试。
- Runtime 无 XML parser/formatter/tool-call prompt 残留；一次性迁移程序不被 runtime import，也不随服务发布。
- 无协议 feature flag、legacy compatibility adapter 或自动 XML fallback。
- 所有现存 leaf/agent 已按 semantic/mechanical/reset 规则追加成功 summary boundary；旧事件未被改写，native runtime 不读取 boundary 之前事件。
- 新增历史按完整 assistant/tool 协议闭合；任何事件持久化路径均不能留下 accepted orphan call。
- Admin 明确只提供 messages 级观察，不持久化 native tools schema 快照，不承诺 pre-cutover 审计级重建。
- 目标私有推理端联调通过，失败路径有用户反馈和 ops 日志。
- 活动架构文档与实现一致，feature branch 可一次性整体合入 `main`。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-07-19 | 初版：确定单分支一次性 native cutover；切片仅作为 branch 内施工检查点，不保留双 runtime 或历史兼容层。 |
| 2026-07-20 | 根据设计复核收敛：采用 per-agent progressive state、完整 reasoning 回放、固定 synthetic user reminder、文本 tool result + user image、所有持久化前统一闭合，以及离线 compaction boundary 迁移旧 leaf；明确不保存 schema 快照、不提供 legacy runtime。 |
| 2026-07-27 | 明确 unit 轻量目录与 native schema 双投影；reasoning 统一交由 LiteLLM adapter；以 required-but-tolerant `__reason` 承接调用意图；旧历史改为 active semantic + 全 leaf mechanical + subagent reset，并加入在线生成、SQLite checkpoint、有界并发、ETA 与停写复核；流式调用仅在正常结束后 accepted；token 水位维持 provider usage 驱动的 best-effort 语义，并补齐完整 assistant envelope fallback。 |
