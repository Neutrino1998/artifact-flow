# Model 配置

Model 配置位于 `config/models/models.yaml`。Agent 通过 alias 引用模型，业务提示词不需要知道具体供应商或 endpoint。

## 最小配置

```yaml
models:
  my-model:
    model: gpt-4o
    context_window: 128000
```

然后在 Agent frontmatter 中使用：

```yaml
model: my-model
```

供应商凭证放在 `.env`，例如 `OPENAI_API_KEY`、`DASHSCOPE_API_KEY` 或 `DEEPSEEK_API_KEY`，不要提交到 YAML。

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `models.<alias>.model` | 是 | provider 接受的原始模型 ID，如 `qwen3.7-plus` |
| `context_window` | 是 | 该部署实际生效的总上下文窗口（input + output token）；必须是大于全局 reserve 的正整数 |
| `base_url` | 否 | 自部署或 OpenAI-compatible endpoint |
| `base_url_env` | 否 | 覆盖 `base_url` 的环境变量名；适合 Token Plan 等部署端点 |
| `api_key` | 否 | 显式密钥；通常应改用环境变量 |
| `api_key_env` | 否 | 承载该模型密钥的环境变量名；避免把真实密钥写入 YAML |
| `vision` | 否 | 是否允许 `read_artifact` 向模型发送图片块，默认 `false` |
| `replay_reasoning` | 否 | 是否把历史 assistant 的 `reasoning_content` 回传给该模型，默认 `true` |
| `cache_salt_field` | 否 | 按认证用户隔离 prefix cache 时注入的请求字段名；vLLM 使用 `cache_salt`，不配置则不发送 |
| `params` | 否 | 透传到 OpenAI-compatible request body 的模型参数 |
| `defaults` | 否 | 所有 alias 共用的参数；model 级 `params` 覆盖同名默认值 |

未配置的采样参数不会由 ArtifactFlow 强行补默认值，而是交给模型供应商。常见参数包括 `temperature`、`top_p`、`max_tokens`、`timeout`，以及供应商特有的 `enable_thinking`。

运行时固定使用流式 Chat Completions 并发送
`stream_options.include_usage: true`。正常完成的调用必须返回 usage；缺失时会作为
provider 协议错误响亮失败，因为 input/output token 会直接驱动上下文压缩和资源
监控。ArtifactFlow 不再用本地 tokenizer 猜测兼容端点的 token 用量。

`replay_reasoning` 是 ArtifactFlow 的历史构建策略，不会作为参数传给 provider。设为
`false` 时，推理内容仍会正常流式返回并持久化到事件中，只是不再进入该模型后续调用的
assistant messages；普通回复文本和原生工具调用结构不受影响。需要 preserved/interleaved
thinking 的 Agent 模型应保留默认值 `true`，并另行在 `params` 中配置供应商要求的
thinking 开关；不接受或不需要历史推理的模型可显式设为 `false`。

## 自部署模型

OpenAI-compatible 服务：

```yaml
models:
  internal-qwen:
    model: Qwen3-32B
    context_window: 32768
    base_url: http://model-gateway.internal:8000/v1
    api_key_env: INTERNAL_QWEN_API_KEY
    cache_salt_field: cache_salt
    params:
      temperature: 0.6
      timeout: 900
```

`model` 会原样发送到 endpoint，不使用 `openai/`、`dashscope/` 或其他路由前缀。
Endpoint 优先级是调用方显式参数 > `base_url_env` 指定的非空环境变量 >
`base_url`。因此可以在 YAML 保留标准公网端点，由部署环境切到 Token Plan 或内网网关。
`api_key_env` 指向的变量缺失时会直接报错，不会把变量名当作密钥发送。

`cache_salt_field` 是模型 alias 级开关。配置后，每次普通 Agent、子 Agent 和
compaction 请求都会在 provider request body 中携带该字段。字段值不是原始用户 ID，
而是使用服务端 `ARTIFACTFLOW_JWT_SECRET` 派生的 HMAC-SHA256；同一用户跨请求稳定，
不同用户不同，日志也只记录启用的字段名。配置了该字段但调用链缺少认证用户时会
直接失败，避免静默退化为未隔离缓存。轮换 JWT secret 会使旧 prefix cache 自然失效。

ArtifactFlow 会把 provider 上报的缓存命中量记录为 `cached_input_tokens`，并在普通
会话和管理员会话监控中用 `↻` 展示。vLLM 还必须以
`--enable-prompt-tokens-details` 启动才会返回该明细；未开启或其他 provider 未上报时，
界面不显示缓存数字（与明确上报的 `0` 个命中 token 区分）。
当一个 turn 或管理员会话汇总中只有部分 LLM 调用上报该字段时，汇总值以 `≥`
开头，表示这是已知下界；单次调用的缓存量仍按 provider 原值显示。

Ollama：

```yaml
models:
  local-llama:
    model: llama3
    context_window: 32768
    base_url: http://ollama.internal:11434/v1
    api_key: ollama
```

## Vision

只有显式设置 `vision: true` 的 alias 才会收到图片 Artifact：

```yaml
models:
  vision-model:
    model: qwen3.7-plus
    context_window: 1000000
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: DASHSCOPE_API_KEY
    vision: true
```

这是应用能力声明，不是对供应商能力的自动探测。文本模型误设为 `true` 可能被供应商拒绝；多模态模型漏设则只能收到图片占位说明。

## Context window 与压缩

ArtifactFlow 不从公共模型目录猜测上下文窗口，因为私有部署可能用更小的 serving limit。每个 alias 都必须显式配置 `context_window`，Agent 也必须引用 alias；缺失、非正整数、alias 不存在或窗口不大于 reserve 都会拒绝配置发布。`compact_agent` 的窗口还必须不小于其他任何 Agent，以排除明显容量不足的配置。Release reconcile 会在接触 DB session 前执行这套校验，Backend 启动时再执行一次作为二次防线。

服务级 `ARTIFACTFLOW_COMPACTION_RESERVE_TOKENS` 默认为 `20000`。每个 Agent 的自动压缩阈值为：

```text
context_window - ARTIFACTFLOW_COMPACTION_RESERVE_TOKENS
```

Reserve 为 Agent 后续输出和 compactor 的额外提示、总结输出提供 best-effort headroom，并不保证接近物理窗口上限的压缩一定成功：单次调用可能跨过触发线，不同模型的 tokenizer 和提示开销也可能不同。若 compaction 仍然 overflow，本轮会响亮失败，不会写入假的历史边界、循环压缩或静默丢弃历史。

模型明确返回 context-window overflow 时，引擎会为当前 Agent 立即压缩并重试该次调用一次；重试仍溢出则以错误结束，不会循环压缩。前端的上下文水位上限使用数据库中实际生效的 `lead_agent` 模型对应阈值，Subagent 各自按自己的模型阈值运行。

## 校验

配置文件随进程启动加载。修改后重启 Backend，再从前端发起一个最小对话确认实际连通性。需要逐个真实调用时运行：

```bash
python -m tests.manual.model_providers
```

该命令会调用外部模型并产生费用，不属于普通单元测试。

常见问题：

- alias 拼错会直接报 unknown model，不会静默回退；
- `context_window` 应填写 endpoint 的真实限制；vLLM 场景需与 `max_model_len` 一致；
- `base_url` 必须是 OpenAI-compatible API 根地址；Ollama/vLLM 通常包含 `/v1`，DashScope 使用 `/compatible-mode/v1`；
- `params.timeout` 是等待模型响应数据的 read timeout；连接、写入和连接池等待由 `ARTIFACTFLOW_LLM_*_TIMEOUT` 控制；
- 生产配置修改应通过 [`afctl config`](../operations/releases.md#配置热修)形成新的完整配置快照。
