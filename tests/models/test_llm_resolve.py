"""
_resolve_model_params — 模型名解析的 loud-fail 边界回归

裸名 typo(不在 yaml、无 base_url)必须 loud-fail,别静默当原始 litellm id 去调
(behavior-different)。但两条故意支持的直传路径不能被误伤:provider 前缀格式、
以及 base_url 自部署直传(裸 model + base_url)。后者曾被新加的 guard 打断。
"""

import pytest

from models.llm import _resolve_model_params


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
