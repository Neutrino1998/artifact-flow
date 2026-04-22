# 添加新 Agent

> 一个 Markdown 文件 = 一个 Agent。无需编写 Python 代码。

本指南聚焦操作步骤，Agent 系统的整体设计、YAML schema 完整参考、协作模型等见 [架构文档 · Agent 配置化系统](../architecture/agents.md)。

## 四步流程

### 1. 在 `config/agents/` 创建 `.md` 文件

文件名自由命名（推荐与 `name` 字段一致），如 `translator_agent.md`。文件名不参与加载逻辑，只作识别用途。

### 2. 编写 YAML frontmatter

```yaml
---
name: translator_agent
description: |
  Translation specialist
  - Chinese/English bidirectional translation
  - Preserves tone and technical terminology
tools: {}                        # 空对象表示无工具
model: qwen3.6-plus-no-thinking
max_tool_rounds: 3
---
```

### 3. 编写 Role Prompt（MD body）

紧接在第二个 `---` 之后，纯文本 Markdown，会作为 system prompt 的第一层注入 LLM 调用。

```markdown
<role>
You are translator_agent, a specialized translation agent.

Translate user-provided text between Chinese and English while preserving
the original tone, formatting, and technical terminology.
</role>

<guidelines>
- Do not add commentary or explanations unless asked
- Preserve code blocks, URLs, and markdown formatting verbatim
- For ambiguous terms, pick the most common technical translation
</guidelines>

<output_format>
Return only the translated text, no preamble.
</output_format>
```

### 4. 重启服务

服务启动时 `load_all_agents()` 扫描 `config/agents/` 并加载所有 `.md` 文件。修改后需重启进程才能生效（当前不支持热加载）。

启动日志会出现：`Loaded agent: translator_agent from translator_agent.md`。

## 完整示例

```markdown
---
name: translator_agent
description: |
  Translation specialist — Chinese/English bidirectional
tools: {}
model: qwen3.6-plus-no-thinking
max_tool_rounds: 3
---

<role>
You are translator_agent in a multi-agent team. Translate between Chinese
and English. The Lead Agent decides when to invoke you.
</role>

<guidelines>
- Preserve code, URLs, and markdown formatting
- Preserve technical terms that are conventionally kept in English
- Do not explain your choices unless explicitly asked
</guidelines>

<output_format>
Return only the translated text, wrapped in:

<translation source_lang="..." target_lang="...">
...
</translation>
</output_format>
```

把它放在 `config/agents/translator_agent.md`，重启后 Lead Agent 就能通过 `call_subagent` 调用：

```xml
<tool_call>
  <name>call_subagent</name>
  <params>
    <agent_name><![CDATA[translator_agent]]></agent_name>
    <instruction><![CDATA[Translate to English: 这个系统用 Pi-style 引擎]]></instruction>
  </params>
</tool_call>
```

## Role Prompt 编写建议

- **用 XML 标签分组关注点** — `<role>`, `<guidelines>`, `<output_format>` 等。LLM 对结构化提示词的遵守度更高
- **明确职责边界** — 写清楚"你做什么"和"你不做什么"，特别是与其他 agent 的分工
- **定义输出格式** — subagent 的输出会作为 `<subagent_result>` 打包回传给 Lead，结构化格式便于 Lead 解析整合
- **设置停止条件** — 如 `search_agent` 的"Maximum 3 search iterations"，避免 agent 无限循环调工具
- **利用现有 agent 参考** — `config/agents/search_agent.md` / `crawl_agent.md` 是简洁的范例

## 注意事项

### `internal: true` 的 Agent 不出现在候选列表

设置 `internal: true` 后，agent 不会出现在注入到 `call_subagent` 工具说明中的 `<available_subagents>` 列表 — 换言之，Lead 看不到它，无法主动调用。适用于由代码直接调度的 agent（如 `compact_agent` 由 `CompactionRunner` 在引擎循环内调用）。

### 工具权限覆盖

`tools` 字段是白名单 + per-agent 权限覆盖：

```yaml
tools:
  web_search: auto        # 该 agent 自动执行 web_search
  web_fetch: confirm      # 该 agent 执行 web_fetch 前需用户确认
```

引擎的权限决策（`src/core/engine.py` `_execute_tools`）：agent frontmatter 中显式声明的权限**直接覆盖**工具默认权限；未列出则回退到 `tool.permission`。

**注意：** 覆盖是双向的 — 技术上可以把 `web_fetch` 默认的 CONFIRM 降级为 `auto`。但除非该 agent 已处于受限场景（如内部调度、runbook 固化），否则应只用于**收紧**权限（AUTO → CONFIRM），避免绕过用户确认引入风险。

### 工具白名单

agent 只能调用 `tools` 字段中列出的工具。未列出的工具即使存在于系统中，该 agent 也无法调用（引擎校验会拒绝并反馈错误给 LLM）。

### 文件名与 `name` 字段独立

Agent 以 frontmatter 中的 `name` 字段为准注册到 `{agent_name: AgentConfig}` 字典。两个文件写同一个 `name` 会相互覆盖（按 `sorted()` 顺序，后者胜出），启动日志不会明确报错，建议保持文件名与 `name` 一致避免混淆。

### 单个文件解析失败不影响其他 Agent

YAML 语法错误、缺失必填字段等会被 catch 并 log error，其他 agent 正常加载。启动后发现某个 agent 不可用时先查日志。

### Agent 完成路由

- **Lead Agent** 无工具调用 → 终止引擎循环（这是对话轮次的唯一出口）
- **Subagent** 无工具调用 → 响应打包为 `<subagent_result>` 作为 `call_subagent` 的 tool_result 返回给 Lead

这个不对称设计意味着：不要试图让非 Lead agent "直接输出给用户"，它的输出一定会被包装后回传给 Lead。Lead 才是对用户的唯一出口。

## 下一步

- [添加新工具](./add-tool.md) — 给 agent 配备新能力
- [添加新模型](./add-model.md) — 为 agent 切换 LLM
- [架构 · Agent 配置化系统](../architecture/agents.md) — 完整 schema 与协作模型
