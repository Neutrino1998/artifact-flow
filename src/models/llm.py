"""ArtifactFlow's OpenAI-compatible streaming LLM adapter.

All supported providers expose the OpenAI Chat Completions wire protocol. Model
configuration is loaded from ``models.yaml``; provider-specific request fields
are forwarded unchanged in the JSON body.
"""

import asyncio
import hashlib
import hmac
import inspect
import os
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator, Iterable, Mapping

import httpx
import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai import (
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

load_dotenv()

from config import config as settings
from utils.logger import get_logger
from models.native_tool_stream import NativeToolCallAssembler

logger = get_logger("ArtifactFlow")

# 仅**瞬态/基础设施**错误才重试:网络断连、超时、429 限流、5xx 服务端错误 —— 重试有望
# 成功。只有 provider 提供结构化的 context overflow code 才转成 engine 专用信号；
# 其余确定性失败(BadRequest/400 含「图块发给文本模型」、ContentPolicy、Auth、NotFound
# 等),重试改变不了结果,只拖延 loud-fail + 烧 token,故立即抛。不按错误文本子串猜测。
_RETRYABLE_LLM_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


# ========================================
# 配置加载（模块级缓存）
# ========================================

_config: Optional[Dict[str, Any]] = None
_clients: dict[tuple[Optional[str], Optional[str]], AsyncOpenAI] = {}


class LLMContextOverflowError(RuntimeError):
    """Provider explicitly rejected a call because its context window overflowed."""


class LLMProtocolError(RuntimeError):
    """An OpenAI-compatible endpoint violated ArtifactFlow's required contract."""


def _get_client(base_url: Optional[str], api_key: Optional[str]) -> AsyncOpenAI:
    """Reuse one async HTTP pool per endpoint/credential pair.

    The adapter is the sole retry owner, so the SDK's built-in retries are disabled.
    """
    key = (base_url, api_key)
    client = _clients.get(key)
    if client is None:
        kwargs: dict[str, Any] = {"max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url
        if api_key:
            kwargs["api_key"] = api_key
        client = AsyncOpenAI(**kwargs)
        _clients[key] = client
    return client


async def close_llm_clients() -> None:
    """Close every cached provider connection pool during application shutdown."""
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        await client.close()


def _validate_model_config(
    raw_config: Any,
    required_models: Optional[Iterable[str]] = None,
) -> None:
    """Validate engine-owned model capability metadata.

    ``context_window`` is deliberately required instead of inferred from a public
    model catalog: private deployments can override the serving limit, and a
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
        base_url_env = entry.get("base_url_env")
        if base_url_env is not None and (
            not isinstance(base_url_env, str) or not base_url_env
        ):
            errors.append(f"{alias}: 'base_url_env' must be a non-empty string")

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
    解析模型参数,合并 defaults → model-level params → 构建兼容接口调用参数

    采样参数(temperature/max_tokens/top_p/...) 仅当 yaml 显式配置时才传给 provider;
    没配置就不传 → provider/模型用各自合理默认(OpenAI 1.0、DeepSeek 1.0、vLLM 用
    max_model_len 作为 max_tokens 上界等)。这一层不再强加"温和默认值",避免
    defaults.max_tokens=4096 这类 cap 偷偷咬掉 reasoning 模型的输出。
    上层有 EXECUTION_TIMEOUT + compaction 兜底,不需要这一层再加一道。

    Args:
        model: 模型别名，或在同时给出 base_url 时的 provider model ID
        base_url: 自部署 OpenAI 兼容接口地址
        api_key: API 密钥

    Returns:
        OpenAI-compatible 调用所需的完整参数字典(不含 messages)
    """
    config = _load_config()
    defaults = config.get("defaults", {})
    models = config.get("models", {})

    if model in models:
        model_config = models[model]
        model_id = model_config["model"]
        model_params = model_config.get("params", {})
        # Endpoint 优先级：显式函数参数 > 配置指定的环境变量 >
        # YAML 默认。这让 Token Plan 等部署可通过环境覆盖标准公网端点。
        base_url_env = model_config.get("base_url_env")
        configured_base_url = model_config.get("base_url")
        if not base_url and base_url_env:
            base_url = os.getenv(str(base_url_env))
            if not base_url and not configured_base_url:
                raise ValueError(
                    f"Model '{model}' requires base URL env var '{base_url_env}'"
                )
        base_url = base_url or configured_base_url
        if not api_key:
            api_key = model_config.get("api_key")
        api_key_env = model_config.get("api_key_env")
        if not api_key and api_key_env:
            api_key = os.getenv(str(api_key_env))
            if not api_key:
                raise ValueError(
                    f"Model '{model}' requires API key env var '{api_key_env}'"
                )
    elif base_url:
        # 故意支持的直传路径:给出 base_url 时，model 就是该
        # OpenAI-compatible 端点的原始 model ID，不做 provider 前缀改写。
        model_id = model
        model_params = {}
    else:
        # 裸名 + 不在 models.yaml + 无 base_url —— 几乎必是 typo(写错别名/残留旧别名)。
        # 静默透传会让 SDK 把 typo 当真实 model id 发出 → 用户以为在用 A 实际跑了 B
        # (behavior-different silent fallback)。loud-fail,让 operator 当场发现。
        raise ValueError(
            f"Unknown model '{model}': not a configured alias in models.yaml and no "
            f"base_url was supplied for direct OpenAI-compatible passthrough. "
            f"Available aliases: {sorted(models)}"
        )

    params: dict = {"model": model_id}

    # defaults → model_params(model 级覆盖 defaults 级);
    # 两边都没写的 key 就完全不传,交给 provider/模型默认。
    for key, value in {**defaults, **model_params}.items():
        params[key] = value

    # 自定义 base_url（DashScope/DeepSeek/Ollama/vLLM 等）
    if base_url:
        params["base_url"] = base_url

    if api_key:
        params["api_key"] = api_key

    # timeout 语义分层：模型/defaults 里的数字 timeout 继续表示
    # “等待模型响应数据”（read），connect/write/pool 由服务级隐藏配置
    # 控制。不使用单 float timeout：否则会把为长 TTFT 留的
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


def _split_provider_request(params: dict, model_alias: str) -> tuple[dict, dict]:
    """Separate SDK/client controls from fields forwarded in the request body."""
    client_params = {
        key: params.pop(key)
        for key in ("model", "base_url", "api_key", "timeout")
        if key in params
    }
    nested = params.pop("extra_body", None)
    if nested is None:
        nested = {}
    if not isinstance(nested, dict):
        raise ValueError(f"Model '{model_alias}' params.extra_body must be an object")
    reserved = sorted(
        {"messages", "model", "stream", "stream_options", "tools"}
        .intersection({*params, *nested})
    )
    if reserved:
        raise ValueError(
            f"Model '{model_alias}' params may not override adapter-owned field(s): "
            f"{reserved}"
        )
    duplicates = sorted(set(params).intersection(nested))
    if duplicates:
        raise ValueError(
            f"Model '{model_alias}' defines duplicate provider request field(s) "
            f"inside and outside params.extra_body: {duplicates}"
        )
    return client_params, {**params, **nested}


def _is_context_overflow(error: BadRequestError) -> bool:
    """Recognize only structured provider error codes, never message substrings."""
    codes = {"context_length_exceeded", "context_window_exceeded", "context_length_error"}
    candidates: list[Any] = [getattr(error, "code", None), getattr(error, "type", None)]
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        for source in (body, nested if isinstance(nested, dict) else {}):
            candidates.extend((source.get("code"), source.get("type")))
    return any(value in codes for value in candidates)


async def _close_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


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
        model: 模型别名，或与 base_url 一起使用的 provider model ID
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
    client_params, provider_body = _split_provider_request(params, model)
    cache_note = (
        f" (user cache salt field: {cache_salt_field})"
        if cache_salt_field else ""
    )
    logger.info(f"LLM call: {client_params['model']}{cache_note}")

    last_error = None

    for attempt in range(max_retries):
        stream = None
        stream_started = False
        try:
            client = _get_client(
                client_params.get("base_url"),
                client_params.get("api_key"),
            )
            request: dict[str, Any] = {
                "messages": messages,
                "model": client_params["model"],
                "stream": True,
                "stream_options": {"include_usage": True},
                "timeout": client_params["timeout"],
            }
            if provider_body:
                request["extra_body"] = provider_body
            if tools:
                request["tools"] = tools
            stream = await client.chat.completions.create(**request)

            full_content = ""
            reasoning_content = ""
            token_usage = None
            assembler = NativeToolCallAssembler()
            finish_reasons: list[str] = []

            async for chunk in stream:
                stream_started = True
                # Token usage（通常在最后一个独立 chunk）
                if hasattr(chunk, "usage") and chunk.usage:
                    token_usage = {
                        "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                    }
                    # OpenAI-compatible 端点在 prompt_tokens_details.cached_tokens
                    # 中报告 cache-read token。
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

            # Usage drives compaction and resource metrics. A guessed tokenizer can
            # silently undercount private/provider-specific models, so successful
            # streams must honor include_usage instead of degrading to local estimates.
            if token_usage is None:
                raise LLMProtocolError(
                    f"Model '{model}' completed without usage despite "
                    "stream_options.include_usage=true"
                )
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = token_usage.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise LLMProtocolError(
                        f"Model '{model}' returned invalid usage field '{field}'"
                    )
            cached_input_tokens = token_usage.get("cached_input_tokens")
            if (
                cached_input_tokens is not None
                and (
                    not isinstance(cached_input_tokens, int)
                    or isinstance(cached_input_tokens, bool)
                    or cached_input_tokens < 0
                )
            ):
                raise LLMProtocolError(
                    f"Model '{model}' returned invalid cached input token usage"
                )

            yield {"type": "usage", "token_usage": token_usage}

            yield {
                "type": "final",
                "content": full_content,
                "reasoning_content": reasoning_content or None,
                "tool_calls": tool_calls,
                "token_usage": token_usage,
            }
            return  # 流式完成

        except BadRequestError as e:
            # This is the one deterministic provider rejection the engine can
            # repair structurally: compact the same agent's history and retry the
            # failed invocation once.  Give the engine a provider-neutral type;
            # every other non-retryable error keeps the existing loud-fail path.
            if _is_context_overflow(e):
                raise LLMContextOverflowError(str(e)) from e
            logger.error(f"LLM non-retryable error ({type(e).__name__}): {e}")
            raise

        except _RETRYABLE_LLM_ERRORS as e:
            last_error = e
            if stream_started:
                logger.error(
                    f"LLM stream failed after output began ({type(e).__name__}); "
                    "not retrying a partial response"
                )
                raise
            if isinstance(e, RateLimitError):
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(f"LLM rate limited, retry {attempt+1}/{max_retries} after {wait_time}s")
            elif isinstance(e, APITimeoutError):
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
        finally:
            if stream is not None:
                await _close_stream(stream)

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
        extra_body = params.get("extra_body", {})
        chat_template = (
            extra_body.get("chat_template_kwargs", {})
            if isinstance(extra_body, dict) else {}
        )
        # 推理模型：支持 DashScope enable_thinking 与私有端点的
        # chat_template_kwargs.thinking 两种显式能力声明。
        is_reasoning = bool(
            params.get("enable_thinking", extra_body.get("enable_thinking", False))
            or chat_template.get("thinking", False)
            or "reasoner" in model_config["model"]
        )
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

    The alias-level flag is application metadata, not a provider parameter. Unknown
    direct-passthrough models retain the historical behavior (True); runtime Agents
    are separately required to use configured aliases.
    """
    return get_model_info(model)["replay_reasoning"]
