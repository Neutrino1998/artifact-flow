"""
_resolve_model_params — 模型名解析的 loud-fail 边界回归

裸名 typo(不在 yaml、无 base_url)必须 loud-fail,别静默当原始 litellm id 去调
(behavior-different)。但两条故意支持的直传路径不能被误伤:provider 前缀格式、
以及 base_url 自部署直传(裸 model + base_url)。后者曾被新加的 guard 打断。
"""

import pytest

from models.llm import (
    _resolve_model_params,
    format_messages_for_debug,
    model_supports_vision,
)


def test_known_alias_resolves():
    assert _resolve_model_params("gpt-4o-mini")["model"] == "gpt-4o-mini"


def test_provider_prefixed_passthrough():
    assert _resolve_model_params("deepseek/deepseek-chat")["model"] == "deepseek/deepseek-chat"


def test_bare_unknown_name_loud_fails():
    with pytest.raises(ValueError, match="Unknown model"):
        _resolve_model_params("gpt4o")  # typo, no base_url


def test_bare_model_with_base_url_passes_through():
    """自部署直传:base_url 给定时裸 model 合法,自动加 openai/ 前缀。"""
    params = _resolve_model_params("my-model", base_url="http://localhost:8000/v1")
    assert params["model"] == "openai/my-model"
    assert params["base_url"] == "http://localhost:8000/v1"


def test_bare_model_with_base_url_no_double_prefix():
    """已带已知前缀的 model + base_url 不重复加 openai/。"""
    params = _resolve_model_params("ollama/llama3", base_url="http://localhost:11434/v1")
    assert params["model"] == "ollama/llama3"


# ============================================================
# 识图门控:model_supports_vision（models.yaml `vision: true`）
# ============================================================

def test_vision_flag_true_for_multimodal_alias():
    assert model_supports_vision("qwen3.7-plus") is True
    assert model_supports_vision("gpt-4o") is True


def test_vision_flag_false_for_text_alias():
    assert model_supports_vision("qwen3.7-max") is False


def test_vision_flag_false_for_unknown_alias():
    """未知别名 → False(降级占位,不冒险把图块注入可能不识图的直传模型)。"""
    assert model_supports_vision("totally-made-up") is False


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
# astream_with_retry:仅瞬态错误重试,确定性错误立即 loud-fail
# ============================================================

from litellm.exceptions import BadRequestError, RateLimitError

from models.llm import astream_with_retry


async def _drain(gen):
    return [chunk async for chunk in gen]


async def test_bad_request_fails_fast_no_retry(monkeypatch):
    """BadRequest(400,如图块发给文本模型)是确定性失败 → 不重试,acompletion 只调一次。"""
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        raise BadRequestError(message="bad image block", model="m", llm_provider="p")

    monkeypatch.setattr("models.llm.acompletion", fake_acompletion)
    with pytest.raises(BadRequestError):
        await _drain(astream_with_retry([{"role": "user", "content": "x"}],
                                        model="gpt-4o-mini", max_retries=3, retry_delay=0))
    assert calls["n"] == 1  # 立即抛,无重试


async def test_rate_limit_is_retried(monkeypatch):
    """RateLimitError(429)是瞬态 → 重试到 max_retries 次。"""
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):
        calls["n"] += 1
        raise RateLimitError(message="slow down", llm_provider="p", model="m")

    monkeypatch.setattr("models.llm.acompletion", fake_acompletion)
    with pytest.raises(RateLimitError):
        await _drain(astream_with_retry([{"role": "user", "content": "x"}],
                                        model="gpt-4o-mini", max_retries=3, retry_delay=0))
    assert calls["n"] == 3  # 重试满 3 次才抛
