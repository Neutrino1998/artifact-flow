# 添加新模型

> 三步接入任意 LLM — YAML 配置 + API Key 环境变量 + Agent 引用。基于 [LiteLLM](https://docs.litellm.ai/) 统一接口，覆盖主流云端与自部署模型。

## 三步流程

### 1. 在 `config/models/models.yaml` 添加条目

```yaml
models:
  my-model:                  # alias — agent frontmatter 引用这个名字
    model: provider/model-id # LiteLLM 格式（必填）
    params:                  # 可选，覆盖 defaults
      temperature: 0.5
      max_tokens: 8192
```

### 2. 设置对应 API Key 环境变量

不同 provider 使用不同环境变量（由 LiteLLM 识别）：

| Provider | 环境变量 |
|---------|---------|
| OpenAI | `OPENAI_API_KEY` |
| DashScope (Qwen) | `DASHSCOPE_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Ollama / vLLM / 自定义 | 见下文"自部署模型" |

写入 `.env` 文件，重启服务生效。

### 3. 在 Agent frontmatter 中引用 alias

```yaml
# config/agents/my_agent.md
---
name: my_agent
model: my-model          # 引用 models.yaml 中的 alias
max_tool_rounds: 3
---
```

## 配置结构

`config/models/models.yaml` 分两段：

```yaml
# 所有模型共享的默认参数
defaults:
  temperature: 0.7
  max_tokens: 4096

# 模型列表
models:
  <alias>:
    model: <litellm_format>
    base_url: <可选，自部署模型必填>
    api_key: <可选，多数情况用环境变量>
    params:
      <任意 LiteLLM 支持的参数>
```

**参数合并规则（注意实现限制）：**

- `defaults` 中**只有 `temperature` 和 `max_tokens` 会被继承** — 见 `src/models/llm.py` `_resolve_model_params()`
- 其他默认项（`top_p`、`top_k`、`presence_penalty`、`enable_thinking` 等）放在 `defaults` 下会**静默失效**，必须写在每个模型的 `params` 里
- 模型的 `params` 优先级高于 `defaults`（仅对 `temperature` / `max_tokens` 有效）

### 完整字段参考

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | string | LiteLLM 模型标识，格式 `provider/model-id`（OpenAI 可省略前缀） |
| `base_url` | string | 自定义 API 端点（自部署模型、OpenAI 兼容接口必填） |
| `api_key` | string | 硬编码 API key（不推荐，优先用环境变量） |
| `params.temperature` | float | 采样温度 |
| `params.max_tokens` | int | 最大输出 tokens |
| `params.top_p` / `top_k` | float / int | 核采样参数 |
| `params.presence_penalty` | float | 存在惩罚（降低重复） |
| `params.enable_thinking` | bool | Qwen3 思考模式开关（DashScope 专属） |

其他任意 LiteLLM 支持的参数都可放在 `params` 下，会透传给 provider。

## Provider 示例

### 云端闭源

```yaml
gpt-4o:
  model: gpt-4o                    # OpenAI 可省略 openai/ 前缀

deepseek-reasoner:
  model: deepseek/deepseek-reasoner

qwen3.6-plus:
  model: dashscope/qwen3.6-plus
  params:
    enable_thinking: true
    temperature: 0.6
    top_p: 0.95
```

### Ollama 本地部署

```yaml
local-llama:
  model: ollama/llama3
  base_url: http://localhost:11434/v1
  api_key: ollama                  # Ollama 不校验，占位即可
```

### vLLM 部署

```yaml
my-qwen:
  model: openai/Qwen2-7B-Instruct  # vLLM 走 OpenAI 兼容接口
  base_url: http://localhost:8000/v1
  api_key: token-abc123
```

### 任意 OpenAI 兼容接口

```yaml
my-custom:
  model: my-model                  # 无 provider 前缀时自动加 openai/
  base_url: http://10.0.0.1:8080/v1
  params:
    temperature: 0.5
```

## 验证

### 连通性测试

运行 `tests/manual/litellm_providers.py` 对配置中的每个模型做一次真实调用：

```bash
python tests/manual/litellm_providers.py
```

该脚本不被 pytest 收集（文件名不以 `test_` 开头），需要有效 API key 和网络连接。

### 在 Agent 中试用

把 model alias 填进某个 agent 的 frontmatter，重启服务后发起一个对话，在前端 Observability 模式查看 `llm_complete` 事件。

**注意：** `llm_complete` 事件的 `model` 字段是 agent frontmatter 里的 **alias**，不是解析后的 LiteLLM `provider/model-id`。要确认真正发出去的模型 ID（含 `openai/` 前缀自动补全等行为），用以下方式：

```python
# 在服务进程中（或独立 REPL）
from models.llm import get_litellm_model_id
print(get_litellm_model_id("my-model"))   # 打印真实的 litellm 模型 ID
```

或查看服务 info 级日志，每次 LLM 调用会打印 `LLM call: <resolved-model-id>`（`src/models/llm.py` 中的 `logger.info`，默认 info 级即可见，无需开 `ARTIFACTFLOW_DEBUG`）。

## 常见问题

- **模型找不到** — 检查 `model` 字段的 LiteLLM 格式前缀是否正确（如 `deepseek/` 不能写成 `deepseek-`）
- **认证失败** — 确认环境变量名与 provider 匹配（LiteLLM 的约定，不是 ArtifactFlow 自定义）
- **自部署连不上** — `base_url` 必须包含协议和版本路径（`http://host:port/v1`），Ollama 的 `/v1` 路径也要带
- **Qwen 不走思考模式** — `enable_thinking` 只在 DashScope 路径下生效；若模型本身不支持会被 provider 忽略
