"""
统一的LLM接口封装 - 基于 LiteLLM
支持多种模型提供商，包括自部署服务（Ollama/vLLM）
"""

import asyncio
import os
from typing import Optional, Dict, Any, AsyncIterator, Union
from dataclasses import dataclass, field
from dotenv import load_dotenv

from litellm import completion, acompletion

from utils.logger import get_logger

load_dotenv()

logger = get_logger("ArtifactFlow")


# ========================================
# Provider 配置
# ========================================

# Dashscope 需要指定中国区 endpoint
DASHSCOPE_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


# ========================================
# 预定义模型配置
# ========================================

MODEL_CONFIGS = {
    # OpenAI
    "gpt-4o": {
        "model": "gpt-4o",
        "description": "OpenAI GPT-4o",
    },
    "gpt-4o-mini": {
        "model": "gpt-4o-mini",
        "description": "OpenAI GPT-4o Mini",
    },

    # Qwen3.5 系列（混合思考模型，通过 enable_thinking 控制）
    # 官方推荐采样参数：思考模式 temp=0.6/top_p=0.95/top_k=20，非思考模式 temp=0.7/top_p=0.8/top_k=20
    # 闭源
    "qwen3.5-plus": {
        "model": "dashscope/qwen3.5-plus",
        "extra_params": {"enable_thinking": True, "temperature": 0.6, "top_p": 0.95, "top_k": 20},
        "description": "Qwen3.5 Plus（思考模式）",
    },
    "qwen3.5-plus-no-thinking": {
        "model": "dashscope/qwen3.5-plus",
        "extra_params": {"enable_thinking": False, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
        "description": "Qwen3.5 Plus（非思考模式）",
    },
    "qwen3.5-flash": {
        "model": "dashscope/qwen3.5-flash",
        "extra_params": {"enable_thinking": True, "temperature": 0.6, "top_p": 0.95, "top_k": 20},
        "description": "Qwen3.5 Flash（思考模式）",
    },
    "qwen3.5-flash-no-thinking": {
        "model": "dashscope/qwen3.5-flash",
        "extra_params": {"enable_thinking": False, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
        "description": "Qwen3.5 Flash（非思考模式）",
    },
    # 开源
    "qwen3.5-35b-a3b": {
        "model": "dashscope/qwen3.5-35b-a3b",
        "extra_params": {"enable_thinking": True, "temperature": 0.6, "top_p": 0.95, "top_k": 20},
        "description": "Qwen3.5 35B-A3B（思考模式）",
    },
    "qwen3.5-35b-a3b-no-thinking": {
        "model": "dashscope/qwen3.5-35b-a3b",
        "extra_params": {"enable_thinking": False, "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
        "description": "Qwen3.5 35B-A3B（非思考模式）",
    },

    # DeepSeek
    "deepseek-chat": {
        "model": "deepseek/deepseek-chat",
        "description": "DeepSeek Chat",
    },
    "deepseek-reasoner": {
        "model": "deepseek/deepseek-reasoner",
        "description": "DeepSeek Reasoner (R1)",
    },
}


# ========================================
# 响应数据结构
# ========================================

@dataclass
class LLMResponse:
    """统一的 LLM 响应格式"""
    content: str
    reasoning_content: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = None
    raw_response: Any = None


# ========================================
# 核心 LLM 类
# ========================================

class UnifiedLLM:
    """
    统一的 LLM 接口

    支持三种使用方式：
    1. 预定义模型：create_llm("deepseek-chat")
    2. LiteLLM 格式：create_llm("deepseek/deepseek-chat")
    3. 自定义 OpenAI 兼容接口：create_llm("my-model", base_url="http://localhost:11434/v1", api_key="ollama")
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        streaming: bool = False,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs
    ):
        # 解析模型配置
        if model in MODEL_CONFIGS:
            config = MODEL_CONFIGS[model]
            self.model_name = config["model"]
            # 从预定义配置中合并 extra_params（如 enable_thinking）
            config_extra = config.get("extra_params", {})
            kwargs = {**config_extra, **kwargs}  # 调用方的 kwargs 优先
        else:
            # 直接使用传入的模型名（LiteLLM 格式或自定义）
            self.model_name = model

        self.temperature = temperature
        self.max_tokens = max_tokens
        self.streaming = streaming

        # OpenAI 兼容接口配置（用于 Ollama/vLLM 等自部署服务）
        self.base_url = base_url
        self.api_key = api_key

        # 其他参数
        self.extra_params = kwargs

        logger.info(f"Created UnifiedLLM: {self.model_name}" +
                   (f" (base_url={base_url})" if base_url else ""))

    def _build_params(self, messages: list[dict], stream: bool = False) -> dict:
        """构建 LiteLLM 调用参数"""
        params = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
            **self.extra_params,
        }

        # Dashscope 需要指定中国区 endpoint
        if self.model_name.startswith("dashscope/"):
            params["api_base"] = DASHSCOPE_API_BASE

        # 自定义 base_url（OpenAI 兼容接口）
        if self.base_url:
            params["base_url"] = self.base_url
            # 使用 openai/ 前缀让 LiteLLM 使用 OpenAI SDK
            if not self.model_name.startswith(("openai/", "ollama/", "deepseek/", "dashscope/")):
                params["model"] = f"openai/{self.model_name}"

        # 自定义 API key
        if self.api_key:
            params["api_key"] = self.api_key

        return params

    def _parse_response(self, response) -> LLMResponse:
        """解析 LiteLLM 响应"""
        choice = response.choices[0]
        message = choice.message

        # 获取 reasoning_content（不同 provider 可能位置不同）
        reasoning_content = None
        if hasattr(message, "reasoning_content") and message.reasoning_content:
            reasoning_content = message.reasoning_content

        # 获取 token usage
        token_usage = None
        if response.usage:
            token_usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }

        return LLMResponse(
            content=message.content or "",
            reasoning_content=reasoning_content,
            token_usage=token_usage,
            raw_response=response,
        )

    # ========================================
    # 同步接口
    # ========================================

    def invoke(self, messages: Union[list[dict], str]) -> LLMResponse:
        """
        同步调用

        Args:
            messages: dict 列表或单个字符串

        Returns:
            LLMResponse
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        params = self._build_params(messages)
        response = completion(**params)
        return self._parse_response(response)

    # ========================================
    # 异步接口
    # ========================================

    async def ainvoke(self, messages: Union[list[dict], str]) -> LLMResponse:
        """
        异步调用

        Args:
            messages: dict 列表或单个字符串

        Returns:
            LLMResponse
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        params = self._build_params(messages)
        response = await acompletion(**params)
        return self._parse_response(response)

    async def ainvoke_with_retry(
        self,
        messages: Union[list[dict], str],
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> LLMResponse:
        """
        带重试的异步调用

        Args:
            messages: dict 列表或单个字符串
            max_retries: 最大重试次数
            retry_delay: 初始重试延迟（秒）

        Returns:
            LLMResponse

        Raises:
            Exception: 重试失败后的最后异常
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                return await self.ainvoke(messages)
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # 认证错误不重试
                if "auth" in error_str or ("api" in error_str and "key" in error_str):
                    logger.error(f"LLM authentication error: {e}")
                    raise

                # 计算等待时间
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

    async def astream(
        self,
        messages: Union[list[dict], str]
    ) -> AsyncIterator[dict]:
        """
        异步流式调用

        Yields:
            dict: 包含 type 和 content 的字典
                - {"type": "reasoning", "content": "..."} - 推理内容片段
                - {"type": "content", "content": "..."} - 回答内容片段
                - {"type": "usage", "token_usage": {...}} - Token 使用统计
                - {"type": "final", "content": "...", "reasoning_content": "..."} - 完整响应
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        params = self._build_params(messages, stream=True)

        response = await acompletion(**params)

        full_content = ""
        reasoning_content = ""
        token_usage = None

        async for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 处理 reasoning content
            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                reasoning_content += delta.reasoning_content
                yield {"type": "reasoning", "content": delta.reasoning_content}

            # 处理普通 content
            if delta.content:
                full_content += delta.content
                yield {"type": "content", "content": delta.content}

            # 获取 token usage（通常在最后一个 chunk）
            if hasattr(chunk, "usage") and chunk.usage:
                token_usage = {
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                }

        # 返回 token usage
        if token_usage:
            yield {"type": "usage", "token_usage": token_usage}

        # 返回完整响应
        yield {
            "type": "final",
            "content": full_content,
            "reasoning_content": reasoning_content or None,
            "token_usage": token_usage,
        }

    async def astream_with_retry(
        self,
        messages: Union[list[dict], str],
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> AsyncIterator[dict]:
        """
        带重试的异步流式调用

        只在建立连接阶段重试，流式传输开始后不重试。

        Args:
            messages: dict 列表或单个字符串
            max_retries: 最大重试次数
            retry_delay: 初始重试延迟（秒）

        Yields:
            dict: 同 astream() 的 yield 格式
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                async for chunk in self.astream(messages):
                    yield chunk
                return  # 流式完成，正常退出
            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # 认证错误不重试
                if "auth" in error_str or ("api" in error_str and "key" in error_str):
                    logger.error(f"LLM authentication error: {e}")
                    raise

                # 计算等待时间
                if "rate" in error_str or "limit" in error_str:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.warning(f"LLM stream rate limited, retry {attempt+1}/{max_retries} after {wait_time}s")
                elif "timeout" in error_str:
                    wait_time = retry_delay
                    logger.warning(f"LLM stream timeout, retry {attempt+1}/{max_retries} after {wait_time}s")
                else:
                    wait_time = retry_delay * (1.5 ** attempt)
                    logger.warning(f"LLM stream error: {e}, retry {attempt+1}/{max_retries} after {wait_time}s")

                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                else:
                    raise

        raise last_error or RuntimeError("LLM stream call failed without specific error")


# ========================================
# 便捷函数
# ========================================

def create_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    streaming: bool = False,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    **kwargs
) -> UnifiedLLM:
    """
    创建 LLM 实例

    Args:
        model: 模型名称，支持三种格式：
            - 预定义名称: "qwen3.5-plus", "deepseek-chat" 等
            - LiteLLM 格式: "deepseek/deepseek-chat", "dashscope/qwen3.5-plus" 等
            - 自定义模型: 配合 base_url 使用
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式输出（预留，实际由调用方法决定）
        base_url: OpenAI 兼容接口地址（用于 Ollama/vLLM 等）
        api_key: API 密钥（自部署服务可能需要）
        **kwargs: 其他参数

    Returns:
        UnifiedLLM 实例
    """
    return UnifiedLLM(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
        base_url=base_url,
        api_key=api_key,
        **kwargs
    )


def get_available_models() -> list[str]:
    """获取所有预定义的模型名称"""
    return list(MODEL_CONFIGS.keys())


def get_model_info(model: str) -> Dict[str, Any]:
    """获取模型信息"""
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model]
        return {
            "model_id": config["model"],
            "description": config.get("description", ""),
        }
    return {"model_id": model, "description": "Custom model"}
