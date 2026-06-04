"""
统一的LLM接口 - 基于 LiteLLM

支持多种模型提供商，包括自部署服务（Ollama/vLLM）
模型配置从 models.yaml 加载。
"""

import asyncio
import os
from pathlib import Path
from typing import Optional, Dict, Any, AsyncIterator

import yaml
from dotenv import load_dotenv

# litellm 的 __init__ 会在 import 时联网拉远程 model-cost-map;气隙部署里这个
# HTTP 请求会卡在 getaddrinfo 直到连接超时,而 import 是同步跑在事件循环线程上的
# → 冻住整个 loop(2026-05-14 监控验证时由 deadman dump 定位)。强制用 litellm
# 自带的本地价目表。必须在下面 import litellm 之前设;setdefault 让显式 env 覆盖优先。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from litellm import acompletion

load_dotenv()

from utils.logger import get_logger

logger = get_logger("ArtifactFlow")


# ========================================
# 配置加载（模块级缓存）
# ========================================

_config: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """加载并缓存 models.yaml"""
    global _config
    if _config is None:
        # 从项目根目录 config/models/ 加载
        config_path = Path(__file__).parent.parent.parent / "config" / "models" / "models.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


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
    defaults.max_tokens=4096 这类 cap 偷偷咬掉 reasoning 模型的输出(2026-05-28
    在 intranet 部署上发现 deepseek-v4-flash thinking 输出被精确切在 4096)。
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
        api_key = api_key or model_config.get("api_key")
    elif "/" in model:
        # 原始 litellm 格式(带 provider 前缀,如 deepseek/deepseek-chat、ollama/llama3)。
        # 故意支持,直接透传。
        model_id = model
        model_params = {}
    else:
        # 裸名且不在 models.yaml —— 几乎必是 typo(写错别名/残留旧别名)。
        # 静默透传会让 litellm 拿它当原始 model id 去调 → 用户以为在用 A 实际跑了 B
        # (behavior-different silent fallback)。loud-fail,让 operator 当场发现。
        raise ValueError(
            f"Unknown model '{model}': not a configured alias in models.yaml and not a "
            f"litellm provider-prefixed id (no '/'). Available aliases: {sorted(models)}"
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

    return params


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

    Yields:
        dict: chunk 字典
            - {"type": "reasoning", "content": "..."} - 推理内容片段
            - {"type": "content", "content": "..."} - 回答内容片段
            - {"type": "usage", "token_usage": {...}} - Token 使用统计
            - {"type": "final", "content": "...", "reasoning_content": "..."} - 完整响应
    """
    params = _resolve_model_params(model, base_url, api_key)
    logger.info(f"LLM call: {params['model']}")

    last_error = None

    for attempt in range(max_retries):
        try:
            response = await acompletion(messages=messages, **params)

            full_content = ""
            reasoning_content = ""
            token_usage = None

            async for chunk in response:
                # Token usage（通常在最后一个独立 chunk）
                if hasattr(chunk, "usage") and chunk.usage:
                    token_usage = {
                        "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                        "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                    }

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                    reasoning_content += delta.reasoning_content
                    yield {"type": "reasoning", "content": delta.reasoning_content}

                if delta.content:
                    full_content += delta.content
                    yield {"type": "content", "content": delta.content}

            # Ensure token_usage is always populated — estimate if provider didn't return it
            if not token_usage or token_usage.get("prompt_tokens", 0) == 0:
                try:
                    from litellm import token_counter
                    model_id = params["model"]
                    est_input = token_counter(model=model_id, messages=messages)
                    est_output = token_counter(model=model_id, text=full_content) if full_content else 0
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
                "token_usage": token_usage,
            }
            return  # 流式完成

        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # 认证错误不重试
            if "auth" in error_str or ("api" in error_str and "key" in error_str):
                logger.error(f"LLM authentication error: {e}")
                raise

            if "rate" in error_str or "limit" in error_str:
                wait_time = retry_delay * (2 ** attempt)
                logger.warning(f"LLM rate limited, retry {attempt+1}/{max_retries} after {wait_time}s")
            elif "timeout" in error_str:
                wait_time = retry_delay
                logger.warning(f"LLM timeout, retry {attempt+1}/{max_retries} after {wait_time}s")
            else:
                wait_time = retry_delay * (1.5 ** attempt)
                logger.warning(f"LLM error: {e}, retry {attempt+1}/{max_retries} after {wait_time}s")

            if attempt < max_retries - 1:
                await asyncio.sleep(wait_time)
            else:
                raise

    raise last_error or RuntimeError("LLM call failed without specific error")


# ========================================
# 查询函数
# ========================================

def format_messages_for_debug(messages: list, max_content_len: int = 100000) -> str:
    """格式化消息用于调试输出。截断时附带原始长度,operator 才能分清是完整短消息
    还是被切掉的长消息。"""
    lines = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
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
        }
    return {"model_id": model, "is_reasoning": False}
