"""
_resolve_model_params — 模型名解析的 loud-fail 边界回归

裸名 typo(不在 yaml、无 base_url)必须 loud-fail,别静默当原始 provider
model ID 去调(behavior-different)。base_url 自部署直传(裸 model +
base_url)是故意支持的路径。
"""

import json

import pytest
import httpx
from types import SimpleNamespace
from openai import APIStatusError, AsyncOpenAI

from models.llm import (
    _SDK_MAX_RETRIES,
    _get_client,
    _is_context_overflow,
    _resolve_model_params,
    close_llm_clients,
    format_messages_for_debug,
    get_compaction_threshold,
    get_model_context_window,
    model_replays_reasoning,
    model_supports_vision,
    validate_agent_model_config,
    validate_model_config,
)


def test_known_alias_resolves():
    assert (
        _resolve_model_params("文本模型", api_key="test-key")["model"]
        == "deepseek-v4-flash"
    )


def test_context_window_is_required_for_every_configured_model(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {"models": {"missing-window": {"model": "openai/private"}}},
    )

    with pytest.raises(ValueError, match="context_window.*positive integer"):
        validate_model_config()


def test_replay_reasoning_must_be_boolean(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "bad-replay-flag": {
                    "model": "openai/private",
                    "context_window": 32768,
                    "replay_reasoning": "false",
                }
            }
        },
    )

    with pytest.raises(ValueError, match="replay_reasoning.*boolean"):
        validate_model_config()


def test_agent_models_must_use_configured_aliases(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "configured": {
                    "model": "openai/private",
                    "context_window": 32768,
                }
            }
        },
    )

    with pytest.raises(ValueError, match="must use configured aliases"):
        validate_model_config(["openai/direct-model"])


def test_compact_agent_window_must_cover_every_runtime_agent(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "large": {"model": "openai/large", "context_window": 1_000_000},
                "small": {"model": "openai/small", "context_window": 128_000},
            }
        },
    )

    with pytest.raises(ValueError, match="compact_agent.*at least every Agent"):
        validate_agent_model_config({
            "lead_agent": "large",
            "compact_agent": "small",
        })


def test_compaction_threshold_is_model_window_minus_global_reserve(monkeypatch):
    monkeypatch.setattr("models.llm.settings.COMPACTION_RESERVE_TOKENS", 4096)
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private": {
                    "model": "openai/private",
                    "context_window": 32768,
                }
            }
        },
    )

    assert get_model_context_window("private") == 32768
    assert get_compaction_threshold("private") == 28672


def test_default_timeout_is_split_by_http_phase(monkeypatch):
    """Long model read/TTFT allowance must not also make bad-IP connect slow."""
    monkeypatch.setattr("models.llm.settings.LLM_CONNECT_TIMEOUT", 3.0)
    monkeypatch.setattr("models.llm.settings.LLM_READ_TIMEOUT", 420.0)
    monkeypatch.setattr("models.llm.settings.LLM_WRITE_TIMEOUT", 45.0)
    monkeypatch.setattr("models.llm.settings.LLM_POOL_TIMEOUT", 2.0)

    timeout = _resolve_model_params(
        "文本模型", api_key="test-key"
    )["timeout"]

    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 3.0
    assert timeout.read == 420.0
    assert timeout.write == 45.0
    assert timeout.pool == 2.0


def test_model_timeout_overrides_read_only(monkeypatch):
    """Existing models.yaml params.timeout remains compatible as the read limit."""
    monkeypatch.setattr("models.llm.settings.LLM_CONNECT_TIMEOUT", 4.0)
    monkeypatch.setattr("models.llm.settings.LLM_WRITE_TIMEOUT", 50.0)
    monkeypatch.setattr("models.llm.settings.LLM_POOL_TIMEOUT", 3.0)
    monkeypatch.setattr(
        "models.llm._config",
        {
            "defaults": {},
            "models": {
                "slow-private": {
                    "model": "openai/slow-private",
                    "params": {"timeout": 900},
                }
            },
        },
    )

    timeout = _resolve_model_params("slow-private")["timeout"]

    assert timeout.connect == 4.0
    assert timeout.read == 900.0
    assert timeout.write == 50.0
    assert timeout.pool == 3.0


def test_model_timeout_must_be_positive_number(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "defaults": {},
            "models": {
                "bad-timeout": {
                    "model": "openai/bad-timeout",
                    "params": {"timeout": 0},
                }
            },
        },
    )

    with pytest.raises(ValueError, match="positive number"):
        _resolve_model_params("bad-timeout")


def test_model_api_key_env_resolves_custom_env(monkeypatch):
    monkeypatch.setenv("PRIVATE_MODEL_API_KEY", "secret-from-env")
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-model": {
                    "model": "openai/private-model",
                    "api_key_env": "PRIVATE_MODEL_API_KEY",
                }
            }
        },
    )

    params = _resolve_model_params("private-model")

    assert params["api_key"] == "secret-from-env"


def test_explicit_api_key_overrides_model_api_key_env(monkeypatch):
    monkeypatch.setenv("PRIVATE_MODEL_API_KEY", "secret-from-env")
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-model": {
                    "model": "openai/private-model",
                    "api_key_env": "PRIVATE_MODEL_API_KEY",
                }
            }
        },
    )

    params = _resolve_model_params("private-model", api_key="explicit-secret")

    assert params["api_key"] == "explicit-secret"


def test_base_url_env_overrides_yaml_default(monkeypatch):
    monkeypatch.setenv("PRIVATE_MODEL_BASE", "https://token-plan.example/v1")
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-model": {
                    "model": "provider-model",
                    "base_url": "https://standard.example/v1",
                    "base_url_env": "PRIVATE_MODEL_BASE",
                    "api_key": "endpoint-key",
                }
            }
        },
    )

    params = _resolve_model_params("private-model")

    assert params["base_url"] == "https://token-plan.example/v1"


def test_missing_base_url_env_uses_yaml_default(monkeypatch):
    monkeypatch.delenv("PRIVATE_MODEL_BASE", raising=False)
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-model": {
                    "model": "provider-model",
                    "base_url": "https://standard.example/v1",
                    "base_url_env": "PRIVATE_MODEL_BASE",
                    "api_key": "endpoint-key",
                }
            }
        },
    )

    assert (
        _resolve_model_params("private-model")["base_url"]
        == "https://standard.example/v1"
    )


def test_model_api_key_env_missing_loud_fails(monkeypatch):
    monkeypatch.delenv("PRIVATE_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-model": {
                    "model": "openai/private-model",
                    "api_key_env": "PRIVATE_MODEL_API_KEY",
                }
            }
        },
    )

    with pytest.raises(
        ValueError,
        match="requires API key env var 'PRIVATE_MODEL_API_KEY'",
    ):
        _resolve_model_params("private-model")


def test_provider_prefixed_id_without_endpoint_loud_fails():
    """Provider prefixes were LiteLLM routing metadata, not wire model IDs."""
    with pytest.raises(ValueError, match="no base_url"):
        _resolve_model_params("deepseek/deepseek-chat")


def test_bare_unknown_name_loud_fails():
    with pytest.raises(ValueError, match="Unknown model"):
        _resolve_model_params("gpt4o")  # typo, no base_url


def test_bare_model_with_base_url_passes_through():
    """自部署直传:base_url 给定时裸 model 合法且不改写。"""
    params = _resolve_model_params(
        "my-model",
        base_url="http://localhost:8000/v1",
        api_key="local-no-auth",
    )
    assert params["model"] == "my-model"
    assert params["base_url"] == "http://localhost:8000/v1"
    assert params["api_key"] == "local-no-auth"


def test_model_with_slash_and_base_url_is_still_verbatim():
    params = _resolve_model_params(
        "org/model",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    assert params["model"] == "org/model"


def test_custom_endpoint_config_requires_explicit_endpoint_key(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "unsafe-private": {
                    "model": "private-model",
                    "context_window": 32768,
                    "base_url": "https://private.example/v1",
                }
            }
        },
    )

    with pytest.raises(ValueError, match="custom endpoint requires explicit"):
        validate_model_config()


def test_custom_endpoint_does_not_inherit_global_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leave-this-process")
    monkeypatch.setattr("models.llm._config", {"models": {}})

    with pytest.raises(ValueError, match="requires an explicit api_key"):
        _resolve_model_params(
            "private-model",
            base_url="https://private.example/v1",
        )

    with pytest.raises(ValueError, match="requires an explicit api_key"):
        _get_client("https://private.example/v1", None)


# ============================================================
# 识图门控:model_supports_vision（models.yaml `vision: true`）
# ============================================================

def test_vision_flag_true_for_multimodal_alias():
    assert model_supports_vision("视觉模型") is True


def test_vision_flag_false_for_text_alias():
    assert model_supports_vision("文本模型") is False


def test_vision_flag_false_for_unknown_alias():
    """未知别名 → False(降级占位,不冒险把图块注入可能不识图的直传模型)。"""
    assert model_supports_vision("totally-made-up") is False


# ============================================================
# Reasoning 历史回传:model_replays_reasoning
# ============================================================

def test_replay_reasoning_defaults_true_for_compatibility(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "default-replay": {
                    "model": "openai/private",
                    "context_window": 32768,
                }
            }
        },
    )

    assert model_replays_reasoning("default-replay") is True


def test_replay_reasoning_false_is_app_metadata_not_provider_param(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "defaults": {},
            "models": {
                "no-replay": {
                    "model": "openai/private",
                    "context_window": 32768,
                    "replay_reasoning": False,
                }
            },
        },
    )

    assert model_replays_reasoning("no-replay") is False
    assert "replay_reasoning" not in _resolve_model_params("no-replay")


# ============================================================
# format_messages_for_debug:块列表 content 不崩 + 不吐 base64（P1 回归）
# ============================================================

def test_debug_formatter_handles_image_block_list_without_crash():
    """识图路径的 content 是 [{text}, {image_url}] 块列表;旧实现 .split() 崩。
    且 data-URI 绝不原样落日志(base64 可达数 MB)——只摘 mime + 体量。"""
    big_data_uri = "data:image/png;base64," + ("A" * 100000)
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": big_data_uri}},
        ]},
    ]
    out = format_messages_for_debug(messages)  # must not raise
    assert "look at this" in out
    assert "image/png" in out
    assert "AAAAAAAAAA" not in out  # base64 payload never dumped


def test_debug_formatter_still_handles_plain_string():
    out = format_messages_for_debug([{"role": "user", "content": "hello\nworld"}])
    assert "hello" in out and "world" in out


# ============================================================
# astream_with_retry:SDK 统一负责流建立前的瞬态错误重试
# ============================================================

from openai import BadRequestError, RateLimitError

from models.llm import LLMContextOverflowError, LLMProtocolError, astream_with_retry


async def _drain(gen):
    return [chunk async for chunk in gen]


async def _usage_only_response():
    yield SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        ),
        choices=[],
    )


def _openai_error(error_type, status: int, body: dict, message: str):
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return error_type(message, response=response, body=body)


def _install_create(monkeypatch, create):
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr("models.llm._get_client", lambda *_args: client)


@pytest.mark.parametrize(
    "code",
    [
        "context_length_exceeded",
        "context_window_exceeded",
        "context_length_error",
        "model_context_window_exceeded",
    ],
)
def test_structured_context_overflow_codes(code):
    error = _openai_error(
        BadRequestError,
        400,
        {"error": {"code": code, "message": "provider rejected request"}},
        "provider rejected request",
    )

    assert _is_context_overflow(error) is True


@pytest.mark.parametrize(
    "message",
    [
        "Your input exceeds the context window of this model",
        "Requested token count exceeds the model's maximum context length of 131072 tokens.",
        "Input length (265330) exceeds model's maximum context length (262144).",
        "Input length 131393 exceeds the maximum allowed input length of 131040 tokens.",
        "The input (516368 tokens) is longer than the model's context length (262144 tokens).",
        "The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)",
        "This model's maximum prompt length is 131072 but the request contains 537812 tokens",
        "Please reduce the length of the messages or completion",
        "the request exceeds the available context size, try increasing it",
        "tokens to keep from the initial prompt is greater than the context length",
        "invalid params, context window exceeds limit",
        "Your request exceeded model token limit: 131072 (requested: 140000)",
        "Prompt contains 140000 tokens and is too large for model with 131072 maximum context length",
        "Prompt has 256468 tokens, but the configured context size is 256000 tokens",
        "prompt too long; exceeded max context length by 100918 tokens",
    ],
)
def test_common_openai_compatible_overflow_signatures(message):
    error = _openai_error(
        BadRequestError,
        400,
        {"error": {"code": "invalid_request", "message": message}},
        message,
    )

    assert _is_context_overflow(error) is True


@pytest.mark.parametrize("code", ["InvalidParameter", "invalid_parameter_error"])
def test_dashscope_overflow_requires_code_and_stable_message(code):
    message = "Range of input length should be [1, 129024]"
    error = _openai_error(
        BadRequestError,
        400,
        {"error": {"code": code, "message": message}},
        message,
    )

    assert _is_context_overflow(error) is True


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("InvalidParameter", "The temperature parameter must be between 0 and 2"),
        ("invalid_request", "Range of input length should be [1, 129024]"),
        ("rate_limit", "Too many tokens, rate limit exceeded; please retry"),
        (
            "rate_limit",
            "Rate limit: requested token count exceeds the model's maximum context length of 131072 tokens",
        ),
        ("invalid_request", "The image dimensions exceed the maximum allowed size"),
    ],
)
def test_non_overflow_bad_requests_are_not_misclassified(code, message):
    error = _openai_error(
        BadRequestError,
        400,
        {"error": {"code": code, "message": message}},
        message,
    )

    assert _is_context_overflow(error) is False


async def test_model_cache_salt_is_hmaced_and_sent_via_extra_body(monkeypatch):
    """同用户稳定、跨用户隔离；原始 user_id 不进入 provider request。"""
    monkeypatch.setattr("models.llm.settings.JWT_SECRET", "cache-salt-test-secret")
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-vllm": {
                    "model": "openai/private-vllm",
                    "cache_salt_field": "cache_salt",
                    "params": {"extra_body": {"other_provider_option": True}},
                }
            }
        },
    )
    request_bodies = []

    async def fake_create(**kwargs):
        request_bodies.append(kwargs)
        return _usage_only_response()

    _install_create(monkeypatch, fake_create)
    for user_id in ("user-a", "user-a", "user-b"):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "same prompt"}],
            model="private-vllm",
            user_id=user_id,
        ))

    salts = [body["extra_body"]["cache_salt"] for body in request_bodies]
    assert salts[0] == salts[1]
    assert salts[0] != salts[2]
    assert len(salts[0]) == 64  # HMAC-SHA256 hex，不是原始 user_id
    assert "user-a" not in salts[0]
    assert all(
        body["extra_body"]["other_provider_option"] is True
        for body in request_bodies
    )


async def test_direct_sdk_preserves_messages_and_provider_body(monkeypatch):
    """Exercise real SDK serialization, including non-standard reasoning replay."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        events = [
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "provider-model",
                "choices": [{
                    "index": 0,
                    "delta": {"reasoning_content": "why", "content": "done"},
                    "finish_reason": "stop",
                }],
            },
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "provider-model",
                "choices": [],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        ]
        body = "".join(
            f"data: {json.dumps(event)}\n\n" for event in events
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://provider.example/v1",
        http_client=http_client,
        max_retries=0,
    )
    seen_client_args = []

    def fake_get_client(base_url, api_key):
        seen_client_args.append((base_url, api_key))
        return client

    monkeypatch.setattr("models.llm._get_client", fake_get_client)
    monkeypatch.setattr(
        "models.llm._config",
        {
            "defaults": {},
            "models": {
                "provider": {
                    "model": "provider-model",
                    "base_url": "https://provider.example/v1",
                    "api_key": "secret",
                    "params": {
                        "temperature": 0.6,
                        "extra_body": {"top_k": 20},
                    },
                }
            },
        },
    )
    messages = [{
        "role": "assistant",
        "content": "previous",
        "reasoning_content": "prior reasoning",
    }, {
        "role": "user",
        "content": "continue",
    }]
    try:
        chunks = await _drain(astream_with_retry(messages, model="provider"))
    finally:
        await client.close()

    assert seen_client_args == [("https://provider.example/v1", "secret")]
    assert captured["model"] == "provider-model"
    assert captured["messages"] == messages
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["temperature"] == 0.6
    assert captured["top_k"] == 20
    assert chunks[-1]["reasoning_content"] == "why"
    assert chunks[-1]["token_usage"]["total_tokens"] == 5


async def test_unconfigured_model_does_not_send_cache_salt(monkeypatch):
    monkeypatch.setattr("models.llm._config", {"models": {}})
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _usage_only_response()

    _install_create(monkeypatch, fake_create)
    await _drain(astream_with_retry(
        [{"role": "user", "content": "x"}],
        model="fake",
        base_url="https://provider.example/v1",
        api_key="test-key",
        user_id="user-a",
    ))

    assert "extra_body" not in captured


async def test_configured_cache_salt_without_user_id_loud_fails(monkeypatch):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-vllm": {
                    "model": "openai/private-vllm",
                    "cache_salt_field": "cache_salt",
                }
            }
        },
    )

    with pytest.raises(ValueError, match="has no user_id"):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="private-vllm",
        ))


@pytest.mark.parametrize("field", ["", 1, "cache-salt"])
async def test_invalid_cache_salt_field_loud_fails(monkeypatch, field):
    monkeypatch.setattr(
        "models.llm._config",
        {
            "models": {
                "private-vllm": {
                    "model": "openai/private-vllm",
                    "cache_salt_field": field,
                }
            }
        },
    )

    with pytest.raises(ValueError, match="non-empty identifier"):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="private-vllm",
            user_id="user-a",
        ))


async def test_bad_request_fails_fast_no_retry(monkeypatch):
    """BadRequest(400,如图块发给文本模型)是确定性失败。"""
    calls = {"n": 0}

    async def fake_create(**kwargs):
        calls["n"] += 1
        raise _openai_error(
            BadRequestError,
            400,
            {"error": {"code": "invalid_request"}},
            "bad image block",
        )

    _install_create(monkeypatch, fake_create)
    with pytest.raises(BadRequestError):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="文本模型",
            api_key="test-key",
            user_id="test-user",
        ))
    assert calls["n"] == 1  # 立即抛,无重试


async def test_context_window_error_maps_to_engine_recovery_signal(monkeypatch):
    """Typed overflow is not retried in the adapter; the engine owns one compact+retry."""
    calls = {"n": 0}

    async def fake_create(**kwargs):
        calls["n"] += 1
        raise _openai_error(
            BadRequestError,
            400,
            {"error": {"code": "context_length_exceeded"}},
            "maximum context length exceeded",
        )

    _install_create(monkeypatch, fake_create)
    with pytest.raises(LLMContextOverflowError):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="文本模型",
            api_key="test-key",
            user_id="test-user",
        ))
    assert calls["n"] == 1


@pytest.mark.parametrize("status_code", [408, 409])
async def test_sdk_retries_transient_http_status(monkeypatch, status_code):
    """SDK 覆盖适配器曾漏掉的 408，并保留官方 409 语义。"""
    calls = {"n": 0}
    monkeypatch.setenv("OPENAI_API_KEY", "global-key-must-not-be-forwarded")

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["authorization"] == "Bearer endpoint-key"
        return httpx.Response(
            status_code,
            request=request,
            headers={"retry-after-ms": "1"},
            json={
                "error": {
                    "message": "retry this request",
                    "type": "transient_error",
                    "code": "transient_error",
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    real_client_class = AsyncOpenAI

    def client_factory(**kwargs):
        return real_client_class(http_client=http_client, **kwargs)

    monkeypatch.setattr("models.llm.AsyncOpenAI", client_factory)
    monkeypatch.setattr("models.llm._clients", {})
    try:
        with pytest.raises(APIStatusError):
            await _drain(astream_with_retry(
                [{"role": "user", "content": "x"}],
                model="private-model",
                base_url="https://provider.example/v1",
                api_key="endpoint-key",
            ))
    finally:
        await close_llm_clients()

    assert calls["n"] == _SDK_MAX_RETRIES + 1


def test_native_probe_empty_dashscope_base_uses_default(monkeypatch):
    from tests.manual import native_tool_call_probe as probe

    captured = {}
    client = object()

    def client_factory(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setenv("DASHSCOPE_API_KEY", "probe-key")
    monkeypatch.setenv("DASHSCOPE_API_BASE", "")
    monkeypatch.setattr(probe, "_client", None)
    monkeypatch.setattr(probe, "AsyncOpenAI", client_factory)

    assert probe._get_client() is client
    assert captured["base_url"] == probe._DEFAULT_DASHSCOPE_BASE_URL


async def test_partial_stream_is_never_retried(monkeypatch):
    calls = {"n": 0}

    async def partial_response():
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(
                finish_reason=None,
                delta=SimpleNamespace(
                    content="partial", reasoning_content=None, tool_calls=[]
                ),
            )],
        )
        raise _openai_error(
            RateLimitError,
            429,
            {"error": {"code": "rate_limit"}},
            "midstream",
        )

    async def fake_create(**kwargs):
        calls["n"] += 1
        return partial_response()

    _install_create(monkeypatch, fake_create)
    with pytest.raises(RateLimitError):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="文本模型",
            api_key="test-key",
            user_id="test-user",
        ))
    assert calls["n"] == 1


async def test_success_without_provider_usage_fails_loud(monkeypatch):
    async def no_usage_response():
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(
                finish_reason="stop",
                delta=SimpleNamespace(
                    content="done", reasoning_content=None, tool_calls=[]
                ),
            )],
        )

    async def fake_create(**kwargs):
        return no_usage_response()

    _install_create(monkeypatch, fake_create)
    with pytest.raises(LLMProtocolError, match="completed without usage"):
        await _drain(astream_with_retry(
            [{"role": "user", "content": "x"}],
            model="文本模型",
            api_key="test-key",
            user_id="test-user",
        ))
