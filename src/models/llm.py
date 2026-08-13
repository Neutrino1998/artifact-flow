"""
统一的LLM接口 - 基于 LiteLLM

支持多种模型提供商，包括自部署服务（Ollama/vLLM）
模型配置从 models.yaml 加载。
"""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, Iterable, Mapping

import httpx
import yaml
from dotenv import load_dotenv

# litellm 的 __init__ 会在 import 时联网拉远程 model-cost-map;气隙部署里这个
# HTTP 请求会卡在 getaddrinfo 直到连接超时,而 import 是同步跑在事件循环线程上的
# → 冻住整个 loop。强制用 litellm
# 自带的本地价目表。必须在下面 import litellm 之前设;setdefault 让显式 env 覆盖优先。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from litellm import acompletion
from litellm.exceptions import (
    APIConnectionError,
    ContextWindowExceededError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

load_dotenv()

from config import config as settings
from utils.logger import get_logger
from models.native_tool_stream import NativeToolCallAssembler

logger = get_logger("ArtifactFlow")

# 仅**瞬态/基础设施**错误才重试:网络断连、超时、429 限流、5xx 服务端错误 —— 重试有望
# 成功。ContextWindowExceeded 转成 engine 专用信号（由引擎 compact + retry once）；
# 其余确定性失败(BadRequest/400 含「图块发给文本模型」、ContentPolicy、Auth、NotFound
# 等),重试改变不了结果,只拖延 loud-fail + 烧 token,故立即抛。
# 旧实现按 str(e) 子串匹配,会把 "context length limit" 误判成限流去重试 —— 改用 litellm
# 的**类型化异常**根治。
_RETRYABLE_LLM_ERRORS = (
    APIConnectionError,
    Timeout,
    RateLimitError,
    ServiceUnavailableError,
    InternalServerError,
)


# ========================================
# 配置加载（模块级缓存）
# ========================================

_config: Optional[Dict[str, Any]] = None


class LLMContextOverflowError(RuntimeError):
    """Provider explicitly rejected a call because its context window overflowed."""


def _validate_model_config(
    raw_config: Any,
    required_models: Optional[Iterable[str]] = None,
) -> None:
    """Validate engine-owned model capability metadata.

    ``context_window`` is deliberately required instead of inferred from LiteLLM's
    public-model catalog: private deployments can override the serving limit, and a
    stale/default guess makes compaction fire after the provider has already rejected
    the request.  Validation happens at startup; the accessors below repeat the local
    entry check for scripts/tests that bypass the application lifespan.
    """
    if not isinstance(raw_config, dict):
        raise ValueError("models.yaml root must be an object")
    models = raw_config.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("models.yaml must define a non-empty 'models' object")

    errors: list[str] = []
    for alias, entry in models.items():
        if not isinstance(alias, str) or not alias:
            errors.append(f"invalid model alias {alias!r}")
            continue
        if not isinstance(entry, dict):
            errors.append(f"{alias}: configuration must be an object")
            continue
        model_id = entry.get("model")
        if not isinstance(model_id, str) or not model_id:
            errors.append(f"{alias}: missing required non-empty 'model'")
        context_window = entry.get("context_window")
        if (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window <= 0
        ):
            errors.append(f"{alias}: 'context_window' must be a positive integer")
        elif context_window <= settings.COMPACTION_RESERVE_TOKENS:
            errors.append(
                f"{alias}: context_window ({context_window}) must exceed "
                f"COMPACTION_RESERVE_TOKENS ({settings.COMPACTION_RESERVE_TOKENS})"
            )
        replay_reasoning = entry.get("replay_reasoning", True)
        if not isinstance(replay_reasoning, bool):
            errors.append(f"{alias}: 'replay_reasoning' must be a boolean")

    required = set(required_models or ())
    missing = sorted(name for name in required if name not in models)
    if missing:
        errors.append(
            "agent model(s) must use configured aliases with context_window: "
            + ", ".join(missing)
        )

    if errors:
        raise ValueError(
            "Invalid model configuration — fix before startup:\n  "
            + "\n  ".join(errors)
        )


def _load_config() -> Dict[str, Any]:
    """加载并缓存 models.yaml"""
    global _config
    if _config is None:
        # 从项目根目录 config/models/ 加载
        config_path = Path(__file__).parent.parent.parent / "config" / "models" / "models.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        _validate_model_config(loaded)
        _config = loaded
    return _config


def validate_model_config(required_models: Optional[Iterable[str]] = None) -> None:
    """Startup validation for models.yaml and every agent-referenced alias."""
    _validate_model_config(_load_config(), required_models)


def validate_agent_model_config(agent_models: Mapping[str, str]) -> None:
    """Validate Agent aliases and reject an obviously undersized compactor.

    Requiring its declared window to cover every runtime Agent window is a
    baseline capacity guard, not a promise that compaction always fits.  The
    reserve provides best-effort headroom; a call may still cross the trigger or
    incur different tokenizer/prompt overhead.  Such failures remain loud.
    """
    raw_config = _load_config()
    _validate_model_config(raw_config, agent_models.values())

    compact_alias = agent_models.get("compact_agent")
    if compact_alias is None:
        return

    models = raw_config["models"]
    compact_window = models[compact_alias]["context_window"]
    oversized = sorted(
        (
            agent_name,
            alias,
            models[alias]["context_window"],
        )
        for agent_name, alias in agent_models.items()
        if agent_name != "compact_agent"
        and models[alias]["context_window"] > compact_window
    )
    if oversized:
        details = ", ".join(
            f"{name}={alias} ({window})" for name, alias, window in oversized
        )
        raise ValueError(
            "Invalid model configuration — compact_agent "
            f"'{compact_alias}' context_window ({compact_window}) must be at least "
            f"every Agent context_window; larger Agent(s): {details}"
        )


def get_model_context_window(model: str) -> int:
    """Return the explicitly configured total input+output context window."""
    models = _load_config().get("models", {})
    model_config = models.get(model)
    if not isinstance(model_config, dict):
        raise ValueError(
            f"Model '{model}' has no configured context_window; agents must use a "
            "models.yaml alias"
        )
    context_window = model_config.get("context_window")
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window <= settings.COMPACTION_RESERVE_TOKENS
    ):
        raise ValueError(
            f"Model '{model}' context_window must be an integer greater than "
            f"COMPACTION_RESERVE_TOKENS ({settings.COMPACTION_RESERVE_TOKENS})"
        )
    return context_window


def get_compaction_threshold(model: str) -> int:
    """Effective per-model trigger: total context window minus output reserve."""
    return get_model_context_window(model) - settings.COMPACTION_RESERVE_TOKENS


# ========================================
# 参数解析
# ========================================

def _resolve_model_params(
    model: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> dict:
    """
    解析模型参数,合并 defaults → model-level params → 构建 litellm 调用参数

    采样参数(temperature/max_tokens/top_p/...) 仅当 yaml 显式配置时才传给 litellm;
    没配置就不传 → provider/模型用各自合理默认(OpenAI 1.0、DeepSeek 1.0、vLLM 用
    max_model_len 作为 max_tokens 上界等)。这一层不再强加"温和默认值",避免
    defaults.max_tokens=4096 这类 cap 偷偷咬掉 reasoning 模型的输出。
    上层有 EXECUTION_TIMEOUT + compaction 兜底,不需要这一层再加一道。

    Args:
        model: 模型别名(如 "qwen3.5-plus")或 litellm 格式(如 "deepseek/deepseek-chat")
        base_url: 自部署 OpenAI 兼容接口地址
        api_key: API 密钥

    Returns:
        litellm.acompletion() 所需的完整参数字典(不含 messages)
    """
    config = _load_config()
    defaults = config.get("defaults", {})
    models = config.get("models", {})

    if model in models:
        model_config = models[model]
        model_id = model_config["model"]
        model_params = model_config.get("params", {})
        # YAML 级 base_url/api_key(函数参数优先)
        base_url = base_url or model_config.get("base_url")
        if not api_key:
            api_key = model_config.get("api_key")
        api_key_env = model_config.get("api_key_env")
        if not api_key and api_key_env:
            api_key = os.getenv(str(api_key_env))
            if not api_key:
                raise ValueError(
                    f"Model '{model}' requires API key env var '{api_key_env}'"
                )
    elif "/" in model or base_url:
        # 故意支持的两条直传路径,都不经 yaml:
        #   1. 原始 litellm 格式(带 provider 前缀,如 deepseek/deepseek-chat、ollama/llama3)
        #   2. 自部署直传:给了 base_url 时,裸 model 是自部署 OpenAI 兼容端点的 model id
        #      (由下方自动加 openai/ 前缀)。这是函数签名承诺的用法,不能被 guard 打断。
        model_id = model
        model_params = {}
    else:
        # 裸名 + 不在 models.yaml + 无 base_url —— 几乎必是 typo(写错别名/残留旧别名)。
        # 静默透传会让 litellm 拿它当原始 model id 去调 → 用户以为在用 A 实际跑了 B
        # (behavior-different silent fallback)。loud-fail,让 operator 当场发现。
        raise ValueError(
            f"Unknown model '{model}': not a configured alias in models.yaml, not a "
            f"litellm provider-prefixed id (no '/'), and no base_url for direct passthrough. "
            f"Available aliases: {sorted(models)}"
        )

    params: dict = {
        "model": model_id,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    # defaults → model_params(model 级覆盖 defaults 级);
    # 两边都没写的 key 就完全不传,交给 provider/模型默认。
    for key, value in {**defaults, **model_params}.items():
        params[key] = value

    # 自定义 base_url（Ollama/vLLM 等）
    if base_url:
        params["base_url"] = base_url
        if not model_id.startswith(("openai/", "ollama/", "deepseek/", "dashscope/")):
            params["model"] = f"openai/{model_id}"

    if api_key:
        params["api_key"] = api_key

    # timeout 语义分层：模型/defaults 里的数字 timeout 继续表示
    # “等待模型响应数据”（read），connect/write/pool 由服务级隐藏配置
    # 控制。不直接使用 LiteLLM 的单 float 默认：它会把为长 TTFT 留的
    # read 上限同时套到错 IP 的 TCP connect 上。
    raw_read_timeout = params.get("timeout", settings.LLM_READ_TIMEOUT)
    if (
        not isinstance(raw_read_timeout, (int, float))
        or isinstance(raw_read_timeout, bool)
        or raw_read_timeout <= 0
    ):
        raise ValueError(
            f"Model '{model}' timeout must be a positive number of seconds"
        )
    params["timeout"] = httpx.Timeout(
        connect=settings.LLM_CONNECT_TIMEOUT,
        read=float(raw_read_timeout),
        write=settings.LLM_WRITE_TIMEOUT,
        pool=settings.LLM_POOL_TIMEOUT,
    )

    return params


def _apply_user_cache_salt(
    params: dict,
    model: str,
    user_id: Optional[str],
) -> Optional[str]:
    """按模型配置向 provider request body 注入不透明的用户缓存 salt。

    ``cache_salt_field`` 是 alias 级能力声明，而不是静态 ``params``：字段值必须
    随当前认证用户变化。启用后缺 user_id 直接 loud-fail，不能悄悄退化成未隔离
    请求。salt 使用 JWT secret 做带域分离的 HMAC-SHA256；推理服务只看到不可逆、
    跨副本稳定的 opaque 值，不会收到原始 user_id。JWT secret 轮换只会让旧 prefix
    cache 自然失效，不影响业务数据。

    返回实际注入的字段名；未配置则返回 None。
    """
    models = _load_config().get("models", {})
    model_config = models.get(model)
    if not model_config or "cache_salt_field" not in model_config:
        return None

    field = model_config["cache_salt_field"]
    if (
        not isinstance(field, str)
        or not field
        or not field.isidentifier()
    ):
        raise ValueError(
            f"Model '{model}' cache_salt_field must be a non-empty identifier"
        )
    if not user_id:
        raise ValueError(
            f"Model '{model}' enables cache_salt_field but this LLM call has no user_id"
        )
    if not settings.JWT_SECRET:
        # 正常服务启动已由 validate_config 保证；这里保留局部 loud-fail，避免脚本/
        # 测试绕过 lifespan 时生成一个所有部署相同的弱 salt。
        raise ValueError(
            f"Model '{model}' enables cache_salt_field but ARTIFACTFLOW_JWT_SECRET is unset"
        )

    digest = hmac.new(
        settings.JWT_SECRET.encode("utf-8"),
        b"artifactflow:llm-cache-salt:v1\x00" + user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    existing_extra_body = params.get("extra_body")
    if existing_extra_body is None:
        extra_body: dict = {}
    elif isinstance(existing_extra_body, dict):
        extra_body = dict(existing_extra_body)
    else:
        raise ValueError(
            f"Model '{model}' params.extra_body must be an object when cache_salt_field is enabled"
        )
    extra_body[field] = digest
    params["extra_body"] = extra_body
    return field


def get_litellm_model_id(model_alias: str) -> str:
    """Resolve a model alias to its litellm model ID."""
    params = _resolve_model_params(model_alias)
    return params["model"]


# ========================================
# 流式调用（带重试）
# ========================================

async def astream_with_retry(
    messages: list[dict],
    model: str = "gpt-4o-mini",
    max_retries: int = 3,
    retry_delay: float = 1.0,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    user_id: Optional[str] = None,
) -> AsyncIterator[dict]:
    """
    带重试的异步流式 LLM 调用

    只在建立连接阶段重试，流式传输开始后不重试。

    Args:
        messages: 消息列表
        model: 模型别名或 litellm 格式
        max_retries: 最大重试次数
        retry_delay: 初始重试延迟（秒）
        base_url: 自部署接口地址
        api_key: API 密钥
        user_id: 当前认证用户 ID。模型配置 ``cache_salt_field`` 时用于派生并注入
                 opaque cache salt；未配置时不进入 provider 请求。

    Yields:
        dict: chunk 字典
            - {"type": "reasoning", "content": "..."} - 推理内容片段
            - {"type": "content", "content": "..."} - 回答内容片段
            - {"type": "tool_call_progress", "tool_call_progress": [...]} -
              函数调用的轻量累计进度（不含未完整 arguments）
            - {"type": "usage", "token_usage": {...}} - Token 使用统计
            - {"type": "final", "content": "...", "reasoning_content": "..."} - 完整响应
    """
    params = _resolve_model_params(model, base_url, api_key)
    cache_salt_field = _apply_user_cache_salt(params, model, user_id)
    if tools:
        params["tools"] = tools
    cache_note = (
        f" (user cache salt field: {cache_salt_field})"
        if cache_salt_field else ""
    )
    logger.info(f"LLM call: {params['model']}{cache_note}")

    last_error = None

    for attempt in range(max_retries):
        try:
            response = await acompletion(messages=messages, **params)

            full_content = ""
            reasoning_content = ""
            token_usage = None
            assembler = NativeToolCallAssembler()
            finish_reasons: list[str] = []

            async for chunk in response:
                # Token usage（通常在最后一个独立 chunk）
                if hasattr(chunk, "usage") and chunk.usage:
                    token_usage = {
                        "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                    }
                    # LiteLLM 将 OpenAI-compatible/vLLM、DeepSeek、Anthropic 的
                    # cache-read token 统一到 prompt_tokens_details.cached_tokens。
                    # None 表示 provider 未报告，必须与明确报告的 0 区分；因此只在
                    # 字段实际存在时向下游加入可选键。
                    prompt_details = getattr(
                        chunk.usage, "prompt_tokens_details", None
                    )
                    if isinstance(prompt_details, dict):
                        cached_input_tokens = prompt_details.get("cached_tokens")
                    else:
                        cached_input_tokens = getattr(
                            prompt_details, "cached_tokens", None
                        )
                    if cached_input_tokens is not None:
                        token_usage["cached_input_tokens"] = cached_input_tokens

                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason is not None:
                    finish_reasons.append(str(choice.finish_reason))
                delta = choice.delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    reasoning_content += delta.reasoning_content
                    yield {"type": "reasoning", "content": delta.reasoning_content}

                if delta.content:
                    full_content += delta.content
                    yield {"type": "content", "content": delta.content}

                tool_call_deltas = getattr(delta, "tool_calls", None) or []
                if tool_call_deltas:
                    assembler.add_many(tool_call_deltas)
                    yield {
                        "type": "tool_call_progress",
                        "tool_call_progress": assembler.progress_snapshot(),
                    }

            tool_calls = assembler.accept(finish_reasons)

            # Ensure token_usage is always populated — estimate if provider didn't return it
            if not token_usage or token_usage.get("prompt_tokens", 0) == 0:
                try:
                    from litellm import token_counter
                    model_id = params["model"]
                    est_input = token_counter(model=model_id, messages=messages)
                    output_payload = reasoning_content + full_content
                    if tool_calls:
                        output_payload += json.dumps(
                            tool_calls,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    est_output = (
                        token_counter(model=model_id, text=output_payload)
                        if output_payload else 0
                    )
                    token_usage = {
                        "prompt_tokens": est_input,
                        "completion_tokens": est_output,
                        "total_tokens": est_input + est_output,
                    }
                    logger.debug(f"Estimated token usage via token_counter: {token_usage}")
                except Exception as e:
                    logger.warning(f"Token usage estimation failed: {e}")
                    token_usage = token_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

            yield {"type": "usage", "token_usage": token_usage}

            yield {
                "type": "final",
                "content": full_content,
                "reasoning_content": reasoning_content or None,
                "tool_calls": tool_calls,
                "token_usage": token_usage,
            }
            return  # 流式完成

        except ContextWindowExceededError as e:
            # This is the one deterministic provider rejection the engine can
            # repair structurally: compact the same agent's history and retry the
            # failed invocation once.  Give the engine a provider-neutral type;
            # every other non-retryable error keeps the existing loud-fail path.
            raise LLMContextOverflowError(str(e)) from e

        except _RETRYABLE_LLM_ERRORS as e:
            last_error = e
            if isinstance(e, RateLimitError):
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(f"LLM rate limited, retry {attempt+1}/{max_retries} after {wait_time}s")
            elif isinstance(e, Timeout):
                wait_time = retry_delay
                logger.warning(f"LLM timeout, retry {attempt+1}/{max_retries} after {wait_time}s")
            else:
                wait_time = retry_delay * (1.5 ** attempt)
                logger.warning(
                    f"LLM transient error ({type(e).__name__}): {e}, "
                    f"retry {attempt+1}/{max_retries} after {wait_time}s"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
            else:
                raise

        except Exception as e:
            # 非瞬态 = 确定性失败:重试无意义。立即响亮失败(不烧 token、不拖延诊断)。
            # 含 BadRequest/400(图块发给文本模型即此类)、ContentPolicy、
            # Authentication、NotFound 等。ContextWindowExceeded 已在上面转成
            # engine 可恢复信号。
            logger.error(f"LLM non-retryable error ({type(e).__name__}): {e}")
            raise

    raise last_error or RuntimeError("LLM call failed without specific error")


# ========================================
# 查询函数
# ========================================

def _stringify_debug_content(content) -> str:
    """把一条 message 的 content 渲染成可读字符串。content 可能是 str(常态)或
    识图路径的块列表 ``[{type:text,...}, {type:image_url,...}]`` —— 后者**绝不**把
    data-URI 原样吐出(base64 可达数 MB,会撑爆日志、且每轮 eager 求值),只摘出
    mime + 字节量做摘要。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "image_url":
                url = (block.get("image_url") or {}).get("url", "")
                if url.startswith("data:"):
                    header = url[5:].split(";", 1)[0] or "?"
                    parts.append(f"[image_url: {header}, ~{len(url)} chars data-uri]")
                else:
                    parts.append(f"[image_url: {url[:80]}]")
            else:
                parts.append(f"[{btype}]")
        return "\n".join(parts)
    return str(content)


def format_messages_for_debug(messages: list, max_content_len: int = 100000) -> str:
    """格式化消息用于调试输出。截断时附带原始长度,operator 才能分清是完整短消息
    还是被切掉的长消息。识图块列表先压成摘要字符串(不吐 base64)再走统一截断逻辑。"""
    lines = []
    for msg in messages:
        role = msg["role"]
        content = _stringify_debug_content(msg.get("content", ""))
        if not content:
            continue
        original_len = len(content)
        if original_len > max_content_len:
            content = content[:max_content_len] + f"... (truncated, {original_len} chars total)"
            lines.append(f"> {role} ({original_len} chars, truncated to {max_content_len}):")
        else:
            lines.append(f"> {role}:")
        for line in content.split('\n'):
            lines.append(f"  {line}")
        lines.append("")
    return "\n".join(lines)


def get_available_models() -> list[str]:
    """获取所有预定义的模型别名"""
    config = _load_config()
    return list(config.get("models", {}).keys())


def get_model_info(model: str) -> Dict[str, Any]:
    """获取模型信息"""
    config = _load_config()
    models = config.get("models", {})
    if model in models:
        model_config = models[model]
        params = model_config.get("params", {})
        # 推理模型: enable_thinking=True 或模型名含 reasoner
        is_reasoning = params.get("enable_thinking", False) or "reasoner" in model_config["model"]
        return {
            "model_id": model_config["model"],
            "is_reasoning": is_reasoning,
            "supports_vision": bool(model_config.get("vision", False)),
            "replay_reasoning": model_config.get("replay_reasoning", True),
            "context_window": get_model_context_window(model),
        }
    return {
        "model_id": model,
        "is_reasoning": False,
        "supports_vision": False,
        "replay_reasoning": True,
        "context_window": None,
    }


def model_supports_vision(model: str) -> bool:
    """该模型别名是否声明了多模态(models.yaml `vision: true`)。

    识图门控的唯一判据:read_artifact 注入的图块只进 vision:true 模型的上下文,
    文本模型(如 qwen3.7-max)得占位文本而非图块——既避免 provider 端因不识图块
    报错,也让任意私有部署「配什么模型就有什么能力」而非崩溃。未知/未声明 → False。
    """
    return get_model_info(model)["supports_vision"]


def model_replays_reasoning(model: str) -> bool:
    """Whether assistant reasoning is replayed into this model's message history.

    The alias-level flag is application metadata, not a LiteLLM parameter. Unknown
    direct-passthrough models retain the historical behavior (True); runtime Agents
    are separately required to use configured aliases.
    """
    return get_model_info(model)["replay_reasoning"]
