# Native Tool Call 迁移实施计划

> 状态：阶段 0–4 已完成；阶段 5 的停机历史切换与目标私有端点验收待执行
>
> 创建日期：2026-07-19
>
> 最后更新：2026-07-31
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
- 历史切换：维护窗口内完全停止 backend writer 后为所有既有 leaf 的 lead 生成并追加 compaction boundary；subagent 默认 fresh start，不增加 legacy runtime。
- 工具命名与 schema：收紧为各私有推理端普遍可接受的 native function 约束。
- 工具目录与权限提示：保留 unit 级发现语义；权限提示使用确定性工具名文案，不向业务参数注入控制字段。

建议在基线准备后按 5 个实现阶段推进，但只在一个分支完成最终切换。熟悉现有 engine 的工程师预计约需 **12–20 个工程日**，另需停机窗口内的 active branch 语义摘要生成和私有推理端联调时间；供应商流式 chunk、chat template 差异、存量 conversation 数量和模型摘要吞吐是主要不确定项。其余 leaf 使用机械摘要，不消耗模型调用。完全停机方案有意用更长的维护窗口换取更小、更可验证的一次性迁移程序；阶段 0 的规模报告必须据 semantic task 数量评估窗口是否可接受。

## 进度总览

| 阶段 | 内容 | 状态 | 合并/发布条件 |
|---|---|---|---|
| 0 | Provider native 协议探针与迁移基线 | 已完成 | 五模型独立 probe 通过，停机扫描/checkpoint/runbook 已冻结，不发布 |
| 1 | Native schema、命名约束与流式 codec | 已完成 | branch 内测试通过 |
| 2 | 结构化事件、历史与 compaction 投影 | 已完成 | branch 内测试通过 |
| 3 | Per-agent progressive state | 已完成 | branch 内测试通过 |
| 4 | Engine 端到端切换与终态闭合 | 已完成 | native 主链路完整通过 |
| 5 | 历史切换、删除 XML runtime、联调收尾 | 部分完成 | XML runtime 已删除；历史 boundary、停机 apply 与目标端点 smoke 待完成 |

依赖关系：阶段 0 先确认目标模型的 native wire protocol 可用，再进入主体改造；阶段 1、2、3 可以在纯函数和 fixture 层交错推进；阶段 4 必须等待三者完成；阶段 5 必须等待 native 主链路稳定。

### 2026-07-31 实施记录

- 未保留旧 `parameters` 配置/API/runtime 兼容，也未增加供旧 XML Engine 测试使用的临时投影；阶段 1–4 在同一分支直接落到 native 主链路。
- 工具业务参数统一改为 object-root Draft 2020-12 `input_schema`；统一 exporter 只做深拷贝无损导出，不注入控制属性，根级与嵌套约束在模型侧和运行时语义一致。
- XML tool-call parser、grammar/formatter 和专属测试已删除；模型可读的 XML-like tool-result content 已拆为不参与解析的独立 renderer。
- OpenAPI/前端类型已同步；review 收口后端全量回归为 `1935 passed, 42 skipped`，前端为 `285 passed`，lint 与 production build 通过。
- 阶段 5 尚未完成的工作保持为部署/cutover gate：存量 leaf boundary 生成与幂等 apply、维护窗口演练、目标 raw vLLM 等私有端点 smoke。未完成这些项目之前分支不可整体发布。

## 分支策略

- 从最新 `main` 创建 `codex/native-tool-calls`。
- 所有阶段在该分支内完成，可按阶段提交，便于回看和定位回归。
- 不引入协议 feature flag，不增加 per-model `tool_disclosure` 配置，不保留 XML/native 双 runtime。
- 不把阶段 1–4 的半成品单独合入 `main`；阶段 5 验收完成后整体合并。
- 不改写旧事件；迁移为所有现存 leaf 的 lead history 追加 compaction boundary，lead 不再读取 boundary 之前的 XML 历史。

## 目标

迁移完成后，ArtifactFlow 只使用 native tool-call 协议与模型交互，同时保留现有系统真正有价值的上层能力：

- ArtifactFlow 生成并传入当前可见工具的 schema。
- 推理服务负责 chat template 渲染和 native tool-call 外层解析。
- ArtifactFlow 负责流式增量组装、arguments JSON 解码、参数校验、权限校验、执行和错误反馈。
- Skill/tool 渐进状态按 agent 存入对话分支 metadata，不依赖供应商私有 defer 协议。
- 所有当前可访问的 tool unit 继续以 unit description + 成员名形成轻量目录；只有 loaded 工具向 native `tools` 发送完整 schema。
- Skills、有效工具集、权限中断、事件溯源、compaction 和自愈反馈继续工作。
- Thinking 模型的 tool-calling assistant 以 `content + reasoning_content + tool_calls` 原样回传。
- Native tool schema 与业务 `input_schema` 保持一致，不携带 ArtifactFlow 私有控制参数。
- 新协议中的每个 tool call 在历史和事件中结构闭合。

## 不在本次范围

- 不支持 XML/native 双 runtime 或自动回退。
- 不逐条把旧 XML 调用转换成 native tool calls，也不增加运行时翻译。
- 不保证从迁移前的任意非 leaf 内部消息节点重新分叉；迁移时存在的所有 leaf head 均可继续。
- 不依赖 vLLM/DashScope 等供应商私有的 `defer_loading`、`tool_reference` 扩展。
- 不引入宏大的“统一 schema 平台”；只提供 `BaseTool` native schema 导出和保留 MCP `inputSchema` 结构的派生通路。
- 不把模型原始思维链当作调用理由，也不向用户暴露隐藏 reasoning；旧 `<reason>` 不迁移，权限 UI 使用确定性工具名文案。
- 不持久化每次请求的完整 native tools schema 快照；现有 admin prompt reconstruction 只保证 cutover 后请求的 native messages 正确，不宣称还原包含 tools schema 在内的完整请求。迁移前请求不作正确性承诺，也不增加识别、特殊响应或 legacy formatter。
- 不在首轮实现 provider capability matrix、strict mode 或自动供应商探测。
- 不新增 `tool_disclosure` 模型配置；现有静态 `defer` 即为唯一披露策略输入。

## 可观察结果

完成后应能直接观察到：

1. LLM 请求包含当前有效的 native `tools`，不再包含完整 XML 调用语法。
2. Engine 从 `tool_calls[].function.name/arguments` 执行工具，不再解析 assistant XML。
3. 历史中新的工具调用表现为携带 content/reasoning/tool calls 的 assistant 与对应的 `role=tool` 消息。
4. `read_skill`/`search_tools` 的效果只持久作用于调用它的 agent，并随对话分支继承。
5. Reminder 为所有当前可访问的 unit 展示 unit description、成员名和 loaded/deferred 状态，但不重复完整工具 schema。
6. 模型侧 Schema 与运行时业务 Schema 对同一参数对象语义一致；权限确认不依赖模型生成的隐藏或控制字段。
7. 取消、超时、错误和 subagent 中止后不存在 orphan tool call。
8. Compaction 后最近一组真实 tool call/result 仍保持结构闭合且未被摘要替代。
9. 每次模型调用前都会在完整 tool-result 组之后追加一条 synthetic user reminder；图片也通过这条 user 消息传入。
10. 所有存量 leaf 的 lead 经 semantic 或 mechanical boundary 后可继续，迁移前 lead 事件不再进入模型上下文；subagent 默认 fresh start。
11. XML tool-call parser、调用 grammar/tool-doc formatter 及其专属测试从 runtime 删除；模型可读的 XML-like tool-result envelope 拆为独立 renderer 后保留。

## 已确定的设计原则

### 1. Native 协议不等于把控制权交给推理服务

推理服务只负责把传入的 schema 渲染进模型模板，并把模型输出解析成 native tool-call 字段。ArtifactFlow 仍然拥有：

- 工具是否对当前 agent 可见；
- 工具 schema 的来源；
- JSON arguments 的解码与校验；
- 默认值、枚举、必填项和现有安全约束；
- 权限、确认、执行顺序、错误反馈和审计。

`function.arguments` 仍按 JSON 字符串处理，流式响应必须先按 call/index 组装完成，再 `json.loads` 和校验。

Native `role=tool.content` 保留现有模型可读的 XML-like `<tool_result>` envelope，继续表达 `name`、`success`、`data` 和 `error`。标准 native 协议只负责外层 `role=tool + tool_call_id` 绑定，不限制 content 内部采用 XML-like 文本；ArtifactFlow 也不解析这段结果文本。现有 `format_result()` 从同时承载调用 grammar/tool docs 的 `xml_formatter.py` 拆到独立 result renderer，避免“保留结果格式”被误解为保留 XML tool-call runtime。XML parser 专属的 `parser_warnings` 不迁移；native arguments 的 JSON/参数错误直接进入 `error`。图片只在结果文本中保留说明/引用，实际 image blocks 仍由第 7 节的 synthetic user carrier 承载。

Native protocol 不认识 ArtifactFlow 的 unit，但 unit 仍是应用层的发现、权限和生命周期边界。每次请求对当前 agent 的 `EffectiveToolset` 形成两个投影：

- **轻量目录投影**：所有当前可访问的真实 tool unit 都进入 dynamic reminder，展示 unit description、成员 full name 和 `loaded`/`deferred` 状态。
- **Native schema 投影**：所有 non-deferred 工具，以及已由 `search_tools` 披露的 deferred 工具，进入请求的 `tools` 数组并携带完整 function schema。

每次 LLM 调用只计算一次本次 `native_tools`，它同时作为请求 schema 来源和该次 assistant envelope 的执行可见性闸。Engine 不能改用更宽的 `EffectiveToolset.permissions`，也不能在执行 sibling calls 时因前一个 `read_skill`/`search_tools` 改变了 progressive state 而追溯放行本次未声明的工具。未出现在本次 `native_tools` 的调用不执行，并返回绑定原 `call_id` 的可自愈失败结果；read/search 的效果从同一用户 turn 内的下一次 LLM invocation 开始生效。这个调用级名称集合只在内存中复用已经生成的结果，不持久化 schema snapshot 或新增状态模型。

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
- ArtifactFlow 内部统一使用现有 `reasoning_content` 字段；所有收发都经过唯一 LLM adapter 和 LiteLLM，但不假定任意 provider adapter 都天然正确保留/映射该字段。不要增加 `reasoning_replay_field` 一类 per-model 配置。
- DeepSeek、DashScope/Qwen 和 raw vLLM 的实际兼容性必须由连续 tool-call smoke 验证。若目标端点出现可复现的问题，优先升级 LiteLLM 或在唯一 LLM adapter 边界修复；不要把供应商分支扩散到 engine、history 或模型配置。
- `reasoning_content` 只按 assistant envelope 回放，不作为 permission 文案，也不混入工具参数。
- Token usage 正常路径保持现状：直接采用推理端经 LiteLLM 返回的 `prompt_tokens`、`completion_tokens`、`total_tokens`，再沿用当前 `input_tokens`/`output_tokens` 映射；不新增 reasoning breakdown 的读取、持久化或观测字段。
- 推理端不返回可用 usage 时，沿用现有 fallback，只把 output estimate 的输入补全为 `reasoning_content + content + serialized native tool_calls`，不能继续只计算 content；不新增 `estimated` 标记或 usage/event 字段。该估算只用于现有记账、上下文水位和 compaction 的 best-effort 判断。

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
- 状态随父消息自然继承到下一回合和分支；同一用户 turn 内先 read/search 后，从下一次 LLM invocation 起立即可见，不追溯改变同一 assistant envelope 的 sibling calls；不在每个用户回合重置。
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

ArtifactFlow 每次调用都从当前 registry 生成 OpenAI-compatible schema；推理侧负责模型专用 template/parser。完整 tools schema 快照不是协议正确性所需，本次不持久化；admin 只重建 cutover 后请求的 messages，不建立另一套历史 schema 路径，也不把 messages reconstruction 描述成完整 native 请求取证。

### 4. 结构闭合发生在任何事件持久化之前

硬性不变量：每个已接受的 `LLM_COMPLETE.tool_calls[].id` 必须有且仅有一个 `TOOL_COMPLETE.call_id`。

保持现有生成边界：流式 tool-call delta 只在内存中按 call/index 累积，不能仅因某一时刻 id/name/arguments 看似完整就提前接受。只有 provider stream 正常、非截断地结束，并且组装后的 envelope 通过结构校验，才形成 accepted calls、写入 `LLM_COMPLETE.tool_calls` 并进入执行。结构校验至少要求 call id 唯一且非空、function name 非空、arguments 已完整组装为字符串，并拒绝冲突的 index/id delta；完整 arguments 字符串中的非法 JSON 属于已接受调用的参数错误，应生成绑定 call id 的失败 tool result，而不是把整个 envelope 降为 provisional。

存在 buffered tool-call delta 时，取消、超时、流式错误、截断 finish reason 或结构不完整都不得把它们持久化为 tool calls、执行或制造 START/COMPLETE；已流出的普通 content/reasoning 可沿用现有 partial `LLM_COMPLETE` 语义保存，并由既有终态路径报告 LLM protocol error。若本次完全没有 tool-call delta，非 tool 回复的 finish reason 处理保持现状。此次迁移不新增 raw-delta 事件或另一套 provisional 状态机。

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
- 不给 `COMPACTION_SUMMARY` 增加 carry 字段。EventHistory 从事件结构直接推导：若 boundary 前最近一条同 agent 的 `LLM_COMPLETE` 带结构化 `tool_calls`，就在摘要消息之后原样投影该 assistant 的 `content + reasoning_content + tool_calls`；迁移前 XML 文本事件不满足此条件，自然不会被 carry。
- 随后产生的真实 tool results 正常接在该 assistant 消息之后。

目标历史形态为：

```text
user:      compaction summary
assistant: original content + original reasoning_content + original tool_calls
tool:      real result bound to tool_call_id
```

不伪造用于摘要的 assistant/tool 调用对，也不压缩最近的真实工具结果。

### 6. 旧历史通过完全停机下的 lead compaction boundary 切换

迁移目标只是保留存量对话的主题和当前工作，使用户能从迁移时存在的任意 leaf 继续；明确放弃对迁移前真实模型请求的审计级重建。旧 `MessageEvent` 保持 append-only，不更新、不删除。

为控制模型调用量，迁移脚本生成两类 lead-agent boundary 候选来源；每个 leaf 在 apply 时只选择其中一个：

1. **Semantic summary**：只为每个 conversation 的当前 `active_branch` 调用既有 compaction agent/prompt，保留主题、当前工作、关键事实和下一步。
2. **Mechanical summary**：为所有 leaf 纯机械地沿分支路径读取 display-only 的 `Message.user_input` 与 `Message.response`，复制成角色明确的对话摘要；天然去掉中间 tool-call 过程。保留首个用户问题/标题和最近若干完整 user/assistant 对，超限时插入明确省略标记；单条过大时按固定规则截断。

每个 leaf 最终只追加一个有效 lead boundary：active branch 的 semantic summary 成功时使用 semantic；失败时回退 mechanical；其他 leaf 直接使用 mechanical。一次性迁移不扫描或压缩 subagent 历史：迁移后的 subagent 调用默认 `fresh_start=true`，该 instruction 本身就是历史边界；首次显式使用 `fresh_start=false` 延续迁移前 session 仅作 best-effort，可能看到 legacy XML 文本或因旧上下文过长失败。运行时按 agent 的正常 compaction 语义保持不变。迁移时不存在的任意非 leaf 内部节点不作可继续承诺。

生成和 apply 都在完全停机后进行：

- **停止全部 writer 后扫描**：先启用维护页阻断新请求，等待 active executions 清空，再停止全部 backend 实例并完成数据库快照。随后一次性扫描 `(conversation_id, leaf_message_id, is_active_branch)` manifest；从此直到 native runtime 部署完成，数据库除迁移程序外没有 writer，因此不再引入 head fingerprint、在线重扫或变化 leaf 补算逻辑。
- **Checkpoint/resume**：使用独立 SQLite checkpoint 记录 `migration_id`、conversation/leaf、`summary_kind`（semantic/mechanical）、扫描时 active 状态、summary content、status、attempts 和 error；稳定 task key 必须包含 `summary_kind`，因为 active lead 同时有 semantic 与 mechanical 两个候选。支持 `--resume`，不把一次性迁移状态引入 runtime。吞吐与 ETA 从任务计数和运行时间直接计算，不再持久化独立 latency/hash 字段。
- **有界并发与 ETA**：语义摘要使用 `--concurrency N`，对 429/5xx 有界重试；持续报告 total/completed/success/failed/inflight、滚动吞吐和基于剩余任务数的 ETA。服务保持停机直到 generate、apply、verify 和 native runtime smoke 全部完成。
- **确定性、事务性的成对追加**：最终校验每个 leaf 的 lead 均有候选 boundary 后，把 `COMPACTION_START` 与成功 `COMPACTION_SUMMARY` 作为完整 pair 在同一数据库事务中追加。两条事件的 `event_id` 从 `migration_id + leaf + selected summary task` 确定性派生；若数据库已提交但 checkpoint 未更新，resume 重试命中同一组 event id 而不是追加重复 pair。事务性写入使半对不可表示，确定性 event id 使同一任务的重复 pair 不可表示；缺少成功 boundary 或 apply 失败才阻止部署，semantic 失败本身不阻塞。

Boundary 成为 lead EventHistory 自右向左扫描的终点；lead 不再看到此前 XML event，因此 runtime 不需要 legacy XML parser、formatter 或历史分支。Subagent 依靠默认 `fresh_start=true` 隔离旧 session，而非迁移 boundary。迁移程序可携带独立 legacy 读取逻辑，但 runtime 不得 import 或调用。Cutover 时从缺省空 `agent_progressive_state` 开始，模型需要时重新 read/search。旧事件继续沿用既有 UI 展示；admin 对 cutover 后 `AGENT_START` 的 reconstruction 使用 native messages 投影，迁移前锚点的结果不保证正确，并且不识别旧请求、不返回专属状态、不保留 legacy 投影逻辑。

### 7. Reminder 与多模态使用统一的 synthetic user 消息

`MessageEvent` 继续是持久化真相源；每次请求先由 EventHistory 重新投影 provider messages，再由 ContextManager 注入仅属于本次请求的上下文。不要另存一份 provider message history 并与事件同步。

ContextManager 不再修改最后一条历史消息。每次 LLM 请求都在完整 tool-result 组之后，无条件追加一条新的 synthetic `role=user` message；连续 user message 是允许且已有的系统行为，不增加“可合并时合并”的小分支。

- Reminder text 始终放在这条 synthetic user message，不进入 `role=tool` content。
- OpenAI-compatible 的 `role=tool` 采用文本结果。若工具返回图片，先输出同组所有绑定 call id 的文本 tool results，再把图片 blocks 与 reminder text 一并放入这条 synthetic user 多模态消息。
- 同一 assistant 消息连续调用三个图片工具时，先产生三个绑定 call id 的文本 tool results，再只追加一条 synthetic user message；其中三个图片 block 分别标注 `tool_call_id`、`artifact_id`、version 和 content type。图片 user message 不得插入多调用结果组中间。
- 若模型在后续 round 再次调用图片工具，则每个完整调用组各有自己的 carrier message，按真实轮次交错；新的 carrier 只携带该组图片，不复制所有旧 vision blocks。现有跨回合 vision placeholder 与 text-only 模型降级语义保持不变。
- Synthetic message 不成为新的 MessageEvent 历史事实；实际发送的 reminder 继续随 `AGENT_START` 持久化，以支持 cutover 后请求的 messages 级 admin reconstruction。

### 8. 不向业务参数注入调用理由

标准 native tool call 没有与 function name 平级的逐调用理由字段。把 ArtifactFlow 私有字段注入同一个 arguments 对象会改变 `minProperties`、`maxProperties`、`propertyNames`、组合 Schema 等根级业务约束的语义，因此不迁移旧 XML `<reason>` 参数。

- Native exporter 深拷贝业务 `input_schema` 后原样发送；模型侧和运行时校验同一个参数对象。
- Engine 不提取或删除任何控制属性；所有声明参数均按业务 Schema 校验并交给工具。
- Permission UI、`TOOL_START` 和 orphan closure 使用确定性文案“模型请求调用 X”，不读取隐藏 reasoning，也不要求模型生成额外字段。
- `__reason` 不再是保留名；若业务工具显式声明它，就只是普通业务参数。

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

## 阶段 0：Provider native 协议探针与迁移基线

### 包含

- 从最新 `main` 创建 `codex/native-tool-calls`。
- 记录当前 engine、history、compaction、skills、permissions 和 cancellation 相关测试基线。
- 在改动 Engine 前实现独立的 `tests/manual/native_tool_call_probe.py`，使用项目锁定的同一 LiteLLM 版本和实际 provider 配置直接发请求；不经过 ArtifactFlow runtime，不把探针实现演变成第二套 adapter。
- 先对当前 DashScope key 可访问的 `qwen3.7-plus`、`deepseek-v4-flash`、`glm-5.2`、`kimi-k2.6`、`MiniMax-M2.5` 全部执行文本 native tool-call probe；raw `openai/` + vLLM 保留为目标私有环境的部署前必测项。
- 所有文本模型执行同一最小闭环：首请求以 `stream=true` 发送 OpenAI-compatible `tools`（包含 required `__reason`）→ 组装 assistant `content + reasoning_content? + tool_calls` → 发送绑定原 `tool_call_id` 的 XML-like 文本 `role=tool` result → 追加独立 synthetic user reminder → 发起下一请求。模型返回了 `reasoning_content` 时必须在内存中原样回放；未返回时不伪造。
- 所有文本模型再尝试同轮多调用及 `content + tool_calls` 组合，检查每个 id/name/arguments 的流式归属和回放；模型没有按提示产生该形态只记录行为差异，产生了却无法组装或回放才算协议失败。
- 对具备视觉能力的 `qwen3.7-plus` 与 `kimi-k2.6` 增加图片 carrier probe：先闭合一个或多个文本 `role=tool` result，再通过同一条 synthetic user message 发送带来源标签的一张/多张图片和 reminder，确认下一响应可消费图片且不拒绝消息顺序。
- Probe 记录脱敏后的请求/normalized message 结构、raw chunk 形态、finish reason、usage 是否存在以及 pass/fail；完整 reasoning 只在单次进程内用于回放，不写报告或 fixture。Provider usage 缺失、偶发遗漏 `__reason`、未主动产生多调用属于非阻塞观察项，不新增 runtime usage 或 capability 状态。
- 盘点全部 conversation leaf 和各 conversation 的 `active_branch`，定义停机扫描 manifest 和迁移规模报告；报告必须给出 semantic task 数量，供运维评估完全停机窗口。
- 定义 SQLite checkpoint schema、`--resume`、有界并发、重试、滚动吞吐和 ETA 的 CLI 契约边界；具体 generate 默认值与目标环境吞吐在阶段 5 联调时确定。
- 明确 cutover 维护窗口、active execution drain、全部 backend writer 停止方式、数据库快照和迁移失败回滚流程。

### 不包含

- 不创建 runtime 开关。
- 不把 probe 结果持久化为模型 capability matrix，也不按模型生成 runtime 分支。
- 不先改生产配置或部署默认值。

### 验收

- 分支干净建立，现有相关测试可重复通过。
- 五个 DashScope 候选模型均形成探针报告；任何拟继续声明支持的模型都必须接受 native tools、产出可组装的流式调用，并能消费完整 assistant/tool 后续历史。失败模型须在主体改造前解决 provider/template 配置或从本次支持范围移除；若它是必需模型则阻塞阶段 1。
- 任一模型返回 `reasoning_content` 时，原样回放不会导致第二次请求 400；Qwen 与 Kimi 的单图/多图 synthetic carrier 均被接受。Usage 缺失和 `__reason` 遵循率只进入报告，不作为协议 gate。
- 明确最终还需覆盖至少一个 raw `openai/` + vLLM 实际目标端点；环境当前不可用时列为阶段 5 cutover 阻塞项，而不是据此假定兼容。
- 迁移脚本能在停机数据库上稳定枚举全部 leaf、标记 active branch，且 checkpoint/report 能发现遗漏、未完成任务和摘要失败；checkpoint 的 task identity 能区分同一 active lead 的 semantic/mechanical 候选。

阶段 0 的实现、运行命令、快照规模与模型观察见
[`native-tool-call-stage0-runbook.md`](native-tool-call-stage0-runbook.md)，完整脱敏 provider
报告见 [`native-tool-call-provider-probe-report.json`](native-tool-call-provider-probe-report.json)。
五个 DashScope 候选均通过 wire-protocol gate；Qwen 的可选函数名遵循和视觉内容准确率
被明确保留为模型行为观察项，不与流式组装、history replay 或 image carrier 接受性混为一谈。

## 阶段 1：Native schema、命名约束与流式 codec

### 包含

- 为 `BaseTool` 增加 OpenAI 风格 function schema 导出。
- Builtin/HTTP tool 直接声明 object-root Draft 2020-12 `input_schema`，不保留旧 `ToolParameter`/`parameters` 投影；MCP tool 以服务端返回的 `inputSchema` 为 schema source，在深拷贝上做最小规范化并保留嵌套结构和约束。
- 统一 exporter 深拷贝并无损导出业务 `input_schema`，不注入控制属性；MCP 原始 `inputSchema` 不被修改，并继续作为执行期业务参数校验依据。
- LLM 层支持传入 `tools`，并按 call index/id 组装流式 `tool_calls` delta；LiteLLM/OpenAI 的 name/arguments 明确按 append-only delta 处理，不根据字符串前缀猜测累计快照。
- LLM codec 输出保留 content、reasoning content、tool calls 和 usage；final finish reason 只在 codec 内用于判断 buffered tool calls 是否因截断而不可接受，不新增持久化或观测字段。
- 正常 usage 完全沿用当前 LiteLLM/provider 的 `prompt_tokens`、`completion_tokens`、`total_tokens` 读取与 `input_tokens`/`output_tokens` 映射，不新增 reasoning breakdown 字段或计算分支。
- Provider usage 缺失时，沿用现有 fallback，并把 output estimate 补全为 `reasoning_content + content + serialized native tool_calls`；不新增 `estimated` 标记或 usage/event 字段。
- 任意流式 tool-call delta 在 provider stream 正常、非截断结束且通过 envelope 结构校验前都只存在于内存 buffer；取消、超时、截断或结构错误时不得进入可回放 `LLM_COMPLETE.tool_calls`。
- arguments 完成后进行 JSON 解码，再进入现有参数校验；解析/校验失败产生可供模型自愈的明确 tool error。
- 落实 64 字符工具名约束和入口校验。
- 每次请求从当前 registry 与 per-agent effective tool set 生成通用 OpenAI-compatible schema；不保存 schema 快照，不写 provider template 适配或 per-model reasoning field 配置。
- 保持 `reasoning_content` 为 ArtifactFlow 内部统一字段，收发集中在唯一 LLM adapter/LiteLLM 边界；将阶段 0 观察到的实际 chunk/message 形态固化为 codec fixture，并在阶段 5 通过 ArtifactFlow 完整链路重跑，不能仅凭 LiteLLM 抽象假定 provider mapping 正确。

### 不包含

- Engine 此时不切生产主链路。
- 不实现 XML fallback。

### 验收

- 单调用、多调用、分片 name/arguments、空 content、非法 JSON、重复/乱序 delta、缺失/重复 call id、截断 finish reason 以及流式中途取消均有单元测试；只有正常、非截断 terminal 且结构完整的 envelope 才接受 tool calls，非法 JSON arguments 则形成绑定 call id 的失败结果。
- 所有生成的 schema 可被目标 OpenAI-compatible 接口接受；MCP 的根级及嵌套 schema 约束不因导出丢失，原始 `inputSchema` 不被修改。
- 不合法工具名在配置/写入边界失败，MCP 动态发现按约定跳过并记录 warning。
- DeepSeek reasoning 必须回放且不返回 400；DashScope 与 raw vLLM 经 LiteLLM 使用同一 ArtifactFlow message shape，无 per-model 字段分支。
- Usage fixture 覆盖 provider 正常返回、仅 reasoning、reasoning + content + tool calls，以及 provider usage 缺失；正常路径的既有 input/output 计算保持不变，fallback 不漏算 reasoning/tool-call payload。

## 阶段 2：结构化事件、历史与 compaction 投影

### 包含

- `LLM_COMPLETE` 持久化 tool calls、content、reasoning content 和现有 usage；finish reason 只服务流式 codec 的 accepted-call 判断，不进入事件。
- `TOOL_START/TOOL_COMPLETE` 增加并投影 `call_id`。
- EventHistory 将新事件投影为携带 `content + reasoning_content + tool_calls` 的 assistant，以及逐个绑定 `call_id` 的文本 `role=tool` result。
- 将现有模型可读的 XML-like tool-result renderer 从 XML 调用 grammar/formatter 中拆出；`role=tool.content` 继续保留成功、业务错误和参数错误的现有 envelope，runtime 不解析该文本。XML parser 专属 warnings 随 parser 一起删除。
- 实现并测试 compaction summary 后完整 carry latest real assistant envelope 的逻辑。
- ContextManager 不再假定或修改最后一条消息；每次请求都在完整 tool-result 组之后追加独立 synthetic user reminder。
- 图片工具结果在同组全部文本 tool results 之后，通过同一条 synthetic user message 的带来源标签 image blocks 传入。

### 不包含

- 不伪造 compaction tool call。
- 不压缩最新工具结果。
- 不为迁移前事件保留 legacy XML 解析或专属投影；lead boundary 之前的事件不可见，subagent 默认从 fresh-start instruction 开始。

### 验收

- assistant 可同时包含 content 和 tool calls，也可 content 为空。
- Thinking 模型在同回合后续调用、下一用户回合和 compaction carry 中都收到原始 `reasoning_content`。
- 多 tool-call 的结果严格按 `tool_call_id` 绑定。
- 独立 tool-result renderer 对成功、业务错误和参数错误保持现有 XML-like envelope；EventHistory 只把它作为 `role=tool.content` 文本投影，调用绑定由外层 `tool_call_id` 完成。
- compaction 前后历史语义等价，最近 tool call/result 不重复、不丢失、不 orphan。
- 连续 user 消息、最后一条为 tool、图片与 reminder 共存时均产生同一种追加结构。
- 单图片、图片+文本多工具、同组多图片、跨轮多图片、text-only 降级和 compaction 后图片历史均有 fixture；每个 carrier 只包含其所属调用组的图片。

## 阶段 3：Per-agent progressive state

### 包含

- 在 engine state 与 `Message.metadata` 中增加 `agent_progressive_state` map，按父消息恢复并随当前分支持久化。
- 把当前全局 `active_skills` 行为迁移为 per-agent：`read_skill` 只更新调用 agent 的状态与 `EffectiveToolset`；用户 UI 激活明确写入 `lead_agent`。
- `search_tools` 保持现有模型可见的 `ToolResult` 文本和错误语义，只在成功结果的现有 metadata 中附带本次实际展示的 deferred full names，供 Engine 更新调用 agent 的 disclosed 状态；不另造返回协议或事件字段。
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

- Read/search 后从同一用户 turn 的下一次 LLM invocation 起生效，并在下一回合和分支继续保持；全过程只影响调用 agent。
- Deferred 与 non-deferred unit 都出现在轻量目录；未披露 deferred tool 不进入 native `tools`，non-deferred 与已披露 deferred tool 进入完整 schema。
- 搜索已 loaded 工具只返回“已可用”；搜索 deferred 工具才写入调用 agent 的 `disclosed_tools`。
- 披露不能越过 agent/skill 权限，也不能让已删除工具复活。
- 会话分支只继承父消息 metadata 中各 agent 自己的状态。
- Lead 的 read/search 不改变 subagent 状态，反向亦然；subagent 必须自行 read/search。

## 阶段 4：Engine 端到端切换与终态闭合

### 包含

- `_call_llm` 传入当前 native tools，并返回结构化 tool calls。
- Engine 以 name、arguments、call id 驱动现有权限确认、串行执行、subagent 调用和结果记录；每个调用先经过本次 LLM invocation 实际发送的 native tool-name 集合校验，再做当前权限与业务参数校验。
- 移除 engine 对 assistant XML tool-call parser 的调用。
- 工具校验/权限/执行错误统一返回与真实 call id 绑定的失败 tool result，保留现有自愈语义。
- Permission UI、`TOOL_START` 和前端展示使用确定性文案“模型请求调用 X”，不读取原始 reasoning 或业务参数，也不阻止执行。
- 加入唯一、幂等的 orphan closure helper，并让正常后处理、cooperative cancel、external `CancelledError`、late cancel、shutdown/lease fencing 和 subagent unwind 都在任何事件持久化前经过它。
- `_persist_events` 断言 accepted native calls 已闭合，但不生成修复事件。
- 对仍连接的 SSE 发送 closure 生成的配对事件；transport 已断开时仍保证数据库与后续 replay 闭合。

### 不包含

- 不保留旧 engine 执行路径。
- 不按供应商写多套 executor。

### 验收

- 无工具、单工具、同轮多工具、subagent、权限确认/拒绝、参数错误再自愈均通过端到端测试。
- `search_tools/read_skill` 与尚未披露工具出现在同一 assistant envelope 时，后者不执行并收到可自愈失败；下一次 LLM invocation 才能使用更新后的 native schema。
- 业务工具可合法使用 `reason`、`__reason` 或其他 Schema 属性；Engine 不剥离业务参数。
- 取消发生在执行前、工具执行中、工具之间、subagent 内，以及 timeout/error 时，所有 call id 均恰好一个 COMPLETE。
- External cancel 的直写持久化路径同样满足闭合；provider stream 未正常结束、被截断或未通过 envelope 结构校验时，其 buffered 调用不进入闭合集合，也不被执行。
- Engine 新主链路不 import 或调用 XML tool-call parser。
- 新一轮历史可被目标推理端连续消费，不出现 role/order/template 错误。

## 阶段 5：历史切换、删除 XML runtime、联调收尾

### 包含

- 实现独立的一次性迁移程序：服务完全停机后，为每个 conversation 的 active branch 并发生成 semantic summary，同时为所有 leaf 生成纯机械 user/final-response summary。
- 使用 SQLite checkpoint 支持 `--resume`、含 `summary_kind` 的稳定 task key、有界并发、429/5xx 重试、失败明细、rolling throughput 与 ETA；generate/report 阶段不写 `MessageEvent`。
- 扫描前启用维护页、等待 active executions 清空、停止全部 backend writer 并完成数据库快照；迁移期间不再运行在线变化检测或补算分支。
- 在维护窗口的 apply 阶段按 leaf 事务性追加 lead 的完整 `COMPACTION_START`/`COMPACTION_SUMMARY` pair；两条事件使用由最终任务键派生的确定性 `event_id`，checkpoint 提交状态不确定时可幂等重试。
- Native 部署前验证所有 leaf 的 lead 至少存在一个成功 boundary，任何遗漏或 apply 失败都阻止部署；单纯 semantic 失败不阻塞，稳定回退 mechanical。
- Cutover 后初始化空的 `agent_progressive_state`；旧原始事件继续沿用既有 UI 展示。更新 Admin API/frontend 文案与测试：现有 reconstruction 只保证 cutover 后 native messages 正确，不宣称包含 tools schema 的完整请求取证，也不为 pre-cutover 请求增加 detection、legacy reconstruction 或特殊响应分支。
- 将迁移程序所需的 legacy rendering 与 runtime 隔离；迁移完成并验证后删除 runtime 的 XML tool-call parser、formatter、调用语法 prompt 和专属测试。
- 更新 tools、engine、history、compaction、execution lifecycle 等活动架构文档。
- 更新模型配置示例，明确私有部署必须提供兼容的 native tool-call chat template/parser。
- 对目标私有端点执行 smoke：至少覆盖 DeepSeek thinking、DashScope/Qwen 与 raw `openai/` + vLLM；无法在开发环境执行者必须在部署前目标环境补齐。
- 增加关键诊断日志：schema 拒绝、流式组装失败、arguments 解析失败、orphan closure 触发；用户消息保持脱敏。
- 执行全量后端测试和相关前端测试。

### Cutover 顺序

1. 先完成并验证 native 分支、一次性迁移程序和目标端点 smoke，不部署新 runtime。
2. 进入维护窗口，以维护页阻断新业务/API/SSE；等待 active executions 清空。
3. 停止全部 backend 实例，确认没有应用 writer 后完成数据库快照。此后旧 runtime 不再启动，除迁移程序外数据库保持静止。
4. 扫描全部 leaf，以 checkpoint 生成 active semantic 与全 leaf mechanical 候选；持续报告进度和 ETA，semantic 失败选择 mechanical fallback。
5. 全部 leaf 的 lead 均有最终候选后按确定性完整 pair 执行 apply，幂等重试，并校验每个 leaf 至少一个成功 lead boundary。
6. 校验全部通过后部署 native runtime，在维护状态下完成 smoke；失败时停止新 runtime 并从数据库快照恢复旧版本，不启用双 runtime。

### 不包含

- 不以保留 XML 作为供应商不兼容时的降级方案。
- 不自动修补不兼容 chat template；部署检查应响亮失败并给出诊断。
- 不逐事件改写旧历史，不保证 pre-cutover admin reconstruction 正确，不实现识别、专属错误或特殊标记旧 request/schema 的路径，也不保证从迁移前非 leaf 内部节点新建分支。

### 验收

- 所有扫描到的 leaf 都至少有一个可验证的成功 lead boundary；semantic 失败稳定回退 mechanical，任一 boundary 覆盖或 apply 失败都不会进入 native 部署；同一任务重试不会生成重复 pair。
- Active leaf 的 lead 优先从语义摘要继续当前工作，其他 leaf 的 lead 至少从机械 user/final transcript 继续；lead 不读取 boundary 之前的 XML event。
- Subagent 默认 `fresh_start=true` 的调用从新 instruction 开始；首次显式 `fresh_start=false` 延续迁移前 session 不作跨迁移上下文质量保证。
- Checkpoint 中断后可 resume，已完成的 generate task 不重复模型调用；apply 提交结果不确定时凭确定性 event id 幂等重试。迁移期间 backend 保持停机，不存在在线写入复核路径。
- 代码库中不再存在运行时 XML tool-call 协议路径；离线迁移代码不会被 runtime import。
- 全量测试通过，目标私有端点 smoke 通过。
- 活动文档只描述 native runtime；归档文档保留历史背景但不作为当前行为依据。
- 分支达到可整体合并状态。

## 总体验收矩阵

| 场景 | 必须验证的结果 |
|---|---|
| 无工具回复 | content/reasoning/usage 正常，历史无伪 tool message |
| 单/多工具调用 | name、append-only arguments delta、call id 正确组装，串行执行语义不变 |
| Thinking 回放 | 同回合工具循环、下一用户回合及 compaction carry 都原样回传 assistant `reasoning_content`；DeepSeek/DashScope/vLLM 均经 LiteLLM 使用同一内部字段 |
| Token usage | Provider 正常 usage 与现有 input/output 计算保持不变；无 usage 时仅补全现有 assistant output fallback 对 reasoning/content/tool calls 的估算，不增加 usage 字段 |
| 流式中途取消/截断 | 正常、非截断 terminal 且结构校验通过前的任何 delta 都不进入 `LLM_COMPLETE.tool_calls`、不执行、也不制造 closure pair；已流出 content/reasoning 可按现状保存 |
| 非法 JSON/参数 | 不执行工具，返回绑定 call id 的可自愈错误 |
| 未披露/未声明调用 | 即使名称存在于 unit 目录或同一 envelope 先执行了 read/search，也不执行；下一次 LLM invocation 才使用更新后的 native set |
| 权限拒绝 | 不执行工具，模型收到明确失败结果并可继续 |
| Per-agent deferred search | 搜索前不暴露 schema；搜索后从同一用户 turn 的下一次 LLM invocation 起、跨回合只对调用 agent 保持披露 |
| Unit 目录 | Deferred/non-deferred unit 均展示 description、成员名和 loaded/deferred 状态；无权工具不出现，完整 schema 不在 reminder 重复 |
| Per-agent skill activation | Skill 只激活调用 agent；UI 激活只作用 lead；effective set 始终实时求交 |
| 分支继承 | 新分支继承父 metadata 的 per-agent 状态，不通过事件扫描重建 |
| Synthetic reminder | 无论最后消息角色为何，每次请求都追加独立 user message；连续 user 不触发特殊合并 |
| 多模态结果 | 每组所有文本 tool results 先闭合，再追加一条含该组带来源标签图片与 reminder 的 user message；覆盖同组/跨轮多图片和 text-only 降级 |
| Compaction | 摘要后保留最新真实 assistant content/reasoning/tool calls 和未压缩 tool results |
| Cancel/timeout/error | cooperative、external、late cancel、shutdown/lease fencing 等持久化路径中的 accepted call id 均恰好一个完成结果 |
| Subagent 中止 | caller 的 `call_subagent` 调用被失败闭合 |
| 旧历史迁移 | Active leaf lead 优先 semantic、失败回退 mechanical，其他 leaf lead 使用 mechanical；subagent 默认 fresh start，旧事件保持 append-only |
| 停机迁移恢复 | 完全停机后 generate 可 checkpoint/resume 并报告 ETA；apply 使用确定性 event id 幂等追加完整 pair，不产生同任务重复 pair |
| Admin reconstruction | Cutover 后请求按 native messages 正确重建；不宣称包含历史 tools schema，pre-cutover 结果无正确性保证且无 detection/特殊分支 |
| 阶段 0 provider probe | 五个 DashScope 候选模型完成文本 tool-call/reasoning replay；Qwen、Kimi 完成单图/多图 synthetic carrier；报告区分协议 gate 与模型行为观察项 |
| 私有推理端 | 阶段 5 在 ArtifactFlow 完整链路及至少一个 raw vLLM 目标环境重跑，chat template 接受 schema 与消息序列，流式 parser 输出稳定 |

## 风险与控制

| 风险 | 影响 | 控制方式 |
|---|---|---|
| LiteLLM/供应商流式 chunk 形态差异 | arguments 丢片或 call 归属错误 | 唯一 adapter 明确 append-only delta 契约，codec fixture 覆盖嵌套 JSON 分片与实际响应；最终端点 smoke |
| 私有模型 chat template 不支持标准 tools | 请求失败或模型输出普通文本 | Cutover 前显式端点 smoke 并响亮失败；不引入 runtime capability 探测或 XML fallback |
| Thinking assistant 未回放或 LiteLLM provider mapping 漂移 | DeepSeek 等端点后续请求 400 | 完整 assistant envelope fixture + DeepSeek/DashScope/vLLM smoke；问题收敛在 LiteLLM/LLM adapter |
| Provider 缺失 usage 时 fallback 漏算 reasoning/tool calls | Context 水位偏低，compaction 触发过晚 | 补全现有 assistant output estimate 的输入；不新增标记，也不承诺请求前精确预测 |
| Semantic 摘要失败 | Active branch 当前工作丢失 | 自动回退已经生成的 mechanical summary；记录失败但不单独阻塞 cutover |
| 迁移漏掉 lead leaf | 存量分支无法继续或 lead 看到 legacy XML | 停机扫描、checkpoint、DB 快照、部署前全量 lead boundary 校验 |
| 首次 subagent 调用跨迁移继续旧 session | 模型看到 legacy XML 文本或上下文过长 | 默认 `fresh_start=true` 形成自然边界；显式 `fresh_start=false` 仅作 best-effort，不增加迁移 reset machinery |
| 完全停机迁移耗时超出维护窗口 | 服务不可用时间过长 | 阶段 0 先报告 semantic task 数量；有界并发、checkpoint/resume 与 rolling ETA；规模不可接受时在进入主体改造前重新评估方案 |
| 维护期间仍有 backend writer | 扫描结果与 apply 目标漂移 | 维护页阻断新请求、等待 active executions 清空、停止全部 backend 实例后才允许扫描；迁移程序不实现在线竞态补丁 |
| Progressive state 意外跨 agent 传播 | 扩大 schema/权限面并污染上下文 | metadata map 以 agent_name 为 key，read/search 仅更新调用者，授权实时求交 |
| Unit 目录重复完整 schema | 上下文浪费且 defer 语义模糊 | Reminder 只含 unit description、成员名和状态；完整 schema 只走 native `tools` |
| Compaction carry 重复/遗漏 | tool result orphan 或上下文膨胀 | 边界 fixture + 结构闭合不变量测试 |
| 外部取消绕过正常 dispatcher | 数据库留下 orphan，历史请求被 provider 拒绝 | 所有持久化前调用唯一 closure helper，`_persist_events` 只做 closed assertion |
| 未正常结束或结构不完整的流式 delta 被当成 accepted call | 产生无法重放或错误执行的调用 | 仅正常、非截断 terminal 且 id/name/arguments 结构完整时才把内存 buffer 提升为 `LLM_COMPLETE.tool_calls` |
| Deferred/skill 工具在披露前被同 envelope 调用 | 绕过 native schema 与渐进披露边界 | 执行前按本次请求实际发送的 native tool-name 集合校验；状态变更只作用下一次 LLM invocation |
| Tool-role 图片被 provider 拒绝 | 多模态工具历史不可消费 | `role=tool` 仅文本，图片统一放在全部 results 后的 synthetic user message |
| MCP 外部名称不兼容 | schema 被推理端拒绝 | discovery 时校验并 warning 跳过 |

## 开放项

以下已明确归入阶段 5，不阻塞阶段 0 闭合或阶段 1–4 的架构实现：

- 最终私有部署使用的 raw vLLM 版本、模型和 chat template；DashScope 阶段 0 候选模型已经确定。
- Cutover smoke 使用现有模型调用边界显式执行，不增加启动自检、首次调用探测或 provider capability 状态。
- Mechanical summary 的 token/字符上限、保留最近对数，以及 semantic `--concurrency` 默认值；算法、checkpoint 字段和 fallback 顺序已经确定，具体值随目标模型联调确定。
- 目标部署的可接受维护窗口；阶段 0 已验证规模报告能给出 semantic task 数量，阶段 5 在停机扫描后结合目标模型实测吞吐确认窗口。若不可接受，重新安排 cutover，不给停机脚本补在线同步逻辑。

## 完成定义

只有同时满足以下条件，迁移才算完成：

- 唯一运行时协议为 native tool calls。
- Per-agent deferred disclosure、unit catalog、skills、permissions、subagents、reasoning replay、multimodal、compaction 和取消语义均已迁移并有回归测试。
- Runtime 无 XML tool-call parser、调用 grammar/tool-doc formatter 或调用语法 prompt 残留；独立的模型可读 XML-like tool-result renderer 明确保留且不参与解析。一次性迁移程序不被 runtime import，也不随服务发布。
- 无协议 feature flag、legacy compatibility adapter 或自动 XML fallback。
- 所有现存 leaf 的 lead 已按 semantic/mechanical 规则追加成功 summary boundary；旧事件未被改写，lead 不读取 boundary 之前事件，subagent 依赖默认 fresh start 隔离旧 session。
- 新增历史按完整 assistant/tool 协议闭合；任何事件持久化路径均不能留下 accepted orphan call。
- Admin 只保证 cutover 后请求的 native messages reconstruction，不宣称精确还原 tools schema；pre-cutover 结果不保证正确且不增加识别、专属响应或 legacy 投影路径。
- 目标私有推理端联调通过，失败路径有用户反馈和 ops 日志。
- 活动架构文档与实现一致，feature branch 可一次性整体合入 `main`。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-07-19 | 初版：确定单分支一次性 native cutover；切片仅作为 branch 内施工检查点，不保留双 runtime 或历史兼容层。 |
| 2026-07-20 | 根据设计复核收敛：采用 per-agent progressive state、完整 reasoning 回放、固定 synthetic user reminder、文本 tool result + user image、所有持久化前统一闭合，以及离线 compaction boundary 迁移旧 leaf；明确不保存 schema 快照、不提供 legacy runtime。 |
| 2026-07-27 | 明确 unit 轻量目录与 native schema 双投影；`reasoning_content` 作为内部统一字段并在唯一 LLM adapter/LiteLLM 边界验证；usage 正常路径保持现状；以 required-but-tolerant `__reason` 承接调用意图；MCP 保留原始 `inputSchema` 并从深拷贝派生模型 schema；保留并拆分 XML-like tool-result renderer；旧历史改为 active semantic + 全 leaf mechanical + subagent reset，并加入在线生成、SQLite checkpoint、有界并发、ETA 与停写复核；accepted call 增加非截断与结构完整性门槛；执行以本次 native tool-name 集合为闸；迁移 apply 降为完整 pair + best-effort resume，允许重复成功 boundary；admin 仅保证 cutover 后 messages reconstruction。 |
| 2026-07-28 | 删除无收益的 reasoning breakdown、usage estimate 标记、finish-reason 事件字段、parser warnings 迁移、boundary 半对检测与过细 checkpoint 指标；compaction carry 改为从事件结构推导，`search_tools` 沿用现有 ToolResult 协议；阶段 0 增加五个 DashScope 模型的独立 native protocol probe，并覆盖 reasoning 原样回放、`__reason`、多调用和 Qwen/Kimi 图片 carrier，阶段 5 再做 ArtifactFlow/raw vLLM 完整链路验收。 |
| 2026-07-29 | 迁移执行改为完全停机：维护页阻断新请求、drain active executions、停止全部 backend writer、数据库快照后再 scan/generate/apply/verify；删除在线预生成、head fingerprint、停写重扫和变化 leaf 补算。Checkpoint 仅承担停机窗口内摘要任务恢复，task key 增加 `summary_kind`；boundary pair 使用确定性 event id + 单事务实现幂等重试。阶段 0 已实现五模型 probe、只读 scan/report、独立 checkpoint、cutover runbook 与 `afctl apply --keep-maintenance`；五模型 wire-protocol gate 通过。Review 后进一步将一次性迁移收敛为全 leaf lead boundary，删除 subagent 扫描/reset；subagent 默认 fresh start，首次跨迁移 `fresh_start=false` 仅作 best-effort。 |
| 2026-07-31 | Reviewer 复核后将 LiteLLM/OpenAI name/arguments 明确为 append-only delta，删除基于字符串前缀猜测累计快照的合并；同时取消 `__reason` 私有参数，Schema exporter 改为无损深拷贝，确保根级 JSON Schema 与运行时语义一致，权限提示统一使用确定性工具名文案。 |
