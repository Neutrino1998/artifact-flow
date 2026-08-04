# Model 配置

Model 配置位于 `config/models/models.yaml`。Agent 通过 alias 引用模型，业务提示词不需要知道具体供应商或 endpoint。

## 最小配置

```yaml
models:
  my-model:
    model: openai/gpt-4.1
```

然后在 Agent frontmatter 中使用：

```yaml
model: my-model
```

供应商凭证放在 `.env`，例如 `OPENAI_API_KEY`、`DASHSCOPE_API_KEY` 或 `DEEPSEEK_API_KEY`，不要提交到 YAML。

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `models.<alias>.model` | 是 | LiteLLM 模型 ID，如 `dashscope/qwen3.7-plus` |
| `base_url` | 否 | 自部署或 OpenAI-compatible endpoint |
| `api_key` | 否 | 显式密钥；通常应改用环境变量 |
| `api_key_env` | 否 | 承载该模型密钥的环境变量名；避免把真实密钥写入 YAML |
| `vision` | 否 | 是否允许 `read_artifact` 向模型发送图片块，默认 `false` |
| `cache_salt_field` | 否 | 按认证用户隔离 prefix cache 时注入的请求字段名；vLLM 使用 `cache_salt`，不配置则不发送 |
| `params` | 否 | 透传给 LiteLLM 的模型参数 |
| `defaults` | 否 | 所有 alias 共用的参数；model 级 `params` 覆盖同名默认值 |

未配置的采样参数不会由 ArtifactFlow 强行补默认值，而是交给模型供应商。常见参数包括 `temperature`、`top_p`、`max_tokens`、`timeout`，以及供应商特有的 `enable_thinking`。

## 自部署模型

OpenAI-compatible 服务：

```yaml
models:
  internal-qwen:
    model: Qwen3-32B
    base_url: http://model-gateway.internal:8000/v1
    api_key_env: INTERNAL_QWEN_API_KEY
    cache_salt_field: cache_salt
    params:
      temperature: 0.6
      timeout: 900
```

当 `base_url` 存在且 `model` 没有已知 provider 前缀时，运行时按 OpenAI-compatible 模型处理。
`api_key_env` 指向的变量缺失时会直接报错，不会把变量名当作密钥发送。

`cache_salt_field` 是模型 alias 级开关。配置后，每次普通 Agent、子 Agent 和
compaction 请求都会在 provider request body 中携带该字段。字段值不是原始用户 ID，
而是使用服务端 `ARTIFACTFLOW_JWT_SECRET` 派生的 HMAC-SHA256；同一用户跨请求稳定，
不同用户不同，日志也只记录启用的字段名。配置了该字段但调用链缺少认证用户时会
直接失败，避免静默退化为未隔离缓存。轮换 JWT secret 会使旧 prefix cache 自然失效。

Ollama：

```yaml
models:
  local-llama:
    model: ollama/llama3
    base_url: http://ollama.internal:11434
    api_key: ollama
```

## Vision

只有显式设置 `vision: true` 的 alias 才会收到图片 Artifact：

```yaml
models:
  vision-model:
    model: dashscope/qwen3.7-plus
    vision: true
```

这是应用能力声明，不是对供应商能力的自动探测。文本模型误设为 `true` 可能被供应商拒绝；多模态模型漏设则只能收到图片占位说明。

## 校验

配置文件随进程启动加载。修改后重启 Backend，再从前端发起一个最小对话确认实际连通性。需要逐个真实调用时运行：

```bash
python tests/manual/litellm_providers.py
```

该命令会调用外部模型并产生费用，不属于普通单元测试。

常见问题：

- alias 拼错会直接报 unknown model，不会静默回退；
- `base_url` 取决于 provider：`ollama/*` 使用服务根地址，LiteLLM 会追加原生 `/api/...` 路径；`openai/*` 兼容接口通常包含 `/v1`；
- `params.timeout` 是等待模型响应数据的 read timeout；连接、写入和连接池等待由 `ARTIFACTFLOW_LLM_*_TIMEOUT` 控制；
- 生产配置修改应通过 [`afctl config`](../operations/releases.md#配置热修)形成新的完整配置快照。
