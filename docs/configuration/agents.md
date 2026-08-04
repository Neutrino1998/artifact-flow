# Agent 配置

一个 `config/agents/*.md` 文件定义一个 Agent：YAML frontmatter 描述运行属性，正文是 role prompt。

## 最小示例

```markdown
---
name: translator_agent
description: Translate Chinese and English while preserving formatting.
model: qwen3.7-plus-no-thinking
tools: {}
max_tool_rounds: 3
---

<role>
You translate between Chinese and English.
</role>

Return only the translation unless the user asks for an explanation.
```

## Frontmatter

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `name` | 是 | — | Agent 注册名；应与文件名保持一致 |
| `description` | 否 | `""` | 给 Lead Agent 看的委派说明 |
| `model` | 是 | — | `models.yaml` 中的 alias；不允许绕过 alias 直接填写 LiteLLM ID，因为运行时需要 alias 上必填的 `context_window` |
| `tools` | 否 | `{}` | Builtin 名或 external Tool unit 到 `enabled` / `disabled` 的映射 |
| `max_tool_rounds` | 否 | `3` | 本次 Agent 循环允许的最大工具轮数 |
| `internal` | 否 | `false` | 内部 Agent 不出现在可委派 Subagent 列表中 |

任何一个非隐藏 Agent 文件配置错误都会让加载/reconcile 失败。重复的 Agent `name` 同样会被拒绝。

## 启用工具

```yaml
tools:
  read_artifact: enabled
  web_search: enabled
  crm: enabled
  legacy_toolset: disabled
```

这里表达的是能力成员关系，不是执行许可：

- `enabled`：该 Agent 可获得此 Builtin 或 external Tool unit；
- `disabled`：显式关闭；Skill 激活后可以临时打开它；
- `auto` / `confirm` 不能写在 Agent 文件里，权限级别属于 Tool 定义。

Toolset 或 MCP 以 unit 为授权粒度。引用某个成员的完整名称也会归一化为整个 unit，例如 `crm__search_customer` 最终授予 `crm` unit。

常用 Builtin 名包括：

- Artifact：`create_artifact`、`update_artifact`、`rewrite_artifact`、`read_artifact`、`grep_artifact`
- 协作与网络：`call_subagent`、`web_search`、`web_fetch`
- Sandbox：`bash`、`mount`、`persist`

`search_tools`、`read_skill`、`mount_skill` 会根据 deferred Tool 和 Skill 状态按规则注入，通常不需要手工配置。

## Description 与 Prompt

`description` 应告诉 Lead“什么时候值得委派”，不要复制完整 role prompt。建议写清：

- 适合的问题形状；
- 不适合的简单任务；
- 最终会返回什么；
- 是否需要 `fresh_start=false` 延续之前的 Subagent 上下文。

Lead 的 prompt 应保持 Agent 无关。不要在 `lead_agent.md` 硬编码所有 Subagent 的路由规则，否则新增配置 Agent 时两处会漂移。

正文只描述该 Agent 自己的职责、工作方式、停止条件和输出。Subagent 无工具调用时，其最终文本会包装成结果返回调用方；只有 Lead 的最终文本直接返回用户。

## 生效

开发环境按运行方式执行[本地配置工作流](index.md#本地配置工作流)，不要混用宿主 Python 与 Docker Compose 命令。

生产环境通过配置热修或新应用 Release 生效。不要直接编辑 `.artifactflow/releases/<id>/config`。
