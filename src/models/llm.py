"""
统一的LLM接口封装 - 基于 LiteLLM
支持多种模型提供商，包括自部署服务（Ollama/vLLM）
"""

import os
from typing import Optional, Dict, Any, AsyncIterator, Union
from dataclasses import dataclass, field
from dotenv import load_dotenv

from litellm import completion, acompletion
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

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

    # Qwen (通义千问) - 通过 dashscope
    "qwen-turbo": {
        "model": "dashscope/qwen-turbo",
        "description": "Qwen Turbo (快速)",
    },
    "qwen-plus": {
        "model": "dashscope/qwen-plus",
        "description": "Qwen Plus",
    },
    "qwen-max": {
        "model": "dashscope/qwen-max",
        "description": "Qwen Max",
    },

    # Qwen3 系列
    "qwen3-30b-thinking": {
        "model": "dashscope/qwen3-30b-a3b-thinking-2507",
        "support_reasoning": True,
        "auto_reasoning": True,  # 模型本身自带推理
        "description": "Qwen3-30B 思考模型",
    },
    "qwen3-30b-instruct": {
        "model": "dashscope/qwen3-30b-a3b-instruct-2507",
        "description": "Qwen3-30B 指令模型",
    },
    "qwen3-next-80b-thinking": {
        "model": "dashscope/qwen3-next-80b-a3b-thinking",
        "support_reasoning": True,
        "auto_reasoning": True,  # 模型本身自带推理
        "description": "Qwen3-Next-80B 思考模型",
    },
    "qwen3-next-80b-instruct": {
        "model": "dashscope/qwen3-next-80b-a3b-instruct",
        "description": "Qwen3-Next-80B 指令模型",
    },

    # DeepSeek
    "deepseek-chat": {
        "model": "deepseek/deepseek-chat",
        "description": "DeepSeek Chat",
    },
    "deepseek-reasoner": {
        "model": "deepseek/deepseek-reasoner",
        "support_reasoning": True,
        "auto_reasoning": True,  # 模型本身自带推理，不需要 thinking 参数
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

    def to_langchain_message(self) -> AIMessage:
        """转换为 LangChain AIMessage，保持与原有代码兼容"""
        additional_kwargs = {}
        if self.reasoning_content:
            additional_kwargs["reasoning_content"] = self.reasoning_content

        response_metadata = {}
        if self.token_usage:
            # 转换为原有格式：input_tokens / output_tokens
            response_metadata["token_usage"] = {
                "input_tokens": self.token_usage.get("prompt_tokens", 0),
                "output_tokens": self.token_usage.get("completion_tokens", 0),
            }

        return AIMessage(
            content=self.content,
            additional_kwargs=additional_kwargs,
            response_metadata=response_metadata,
        )


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
            self.support_reasoning = config.get("support_reasoning", False)
            # auto_reasoning: 模型本身就是推理模型，不需要额外的 thinking 参数
            self.auto_reasoning = config.get("auto_reasoning", False)
        else:
            # 直接使用传入的模型名（LiteLLM 格式或自定义）
            self.model_name = model
            self.support_reasoning = kwargs.pop("support_reasoning", False)
            self.auto_reasoning = kwargs.pop("auto_reasoning", False)

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

        # 对支持推理的模型启用 thinking 模式
        # 但跳过 auto_reasoning 的模型（如 DeepSeek reasoner 本身就是推理模型）
        if self.support_reasoning and not self.auto_reasoning:
            params["thinking"] = {"type": "enabled"}

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

    def _format_messages(self, messages: Union[list[BaseMessage], list[dict]]) -> list[dict]:
        """将消息转换为 dict 格式"""
        if not messages:
            return []

        # 如果已经是 dict 格式，直接返回
        if isinstance(messages[0], dict):
            return messages

        # 转换 LangChain 消息
        result = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                role = "system"
            elif isinstance(msg, HumanMessage):
                role = "user"
            elif isinstance(msg, AIMessage):
                role = "assistant"
            elif hasattr(msg, "type"):
                role = {"human": "user", "ai": "assistant", "system": "system"}.get(
                    msg.type, "user"
                )
            else:
                role = "user"
            result.append({"role": role, "content": msg.content})
        return result

    # ========================================
    # 同步接口
    # ========================================

    def invoke(self, messages: Union[list[BaseMessage], list[dict], str]) -> AIMessage:
        """
        同步调用

        Args:
            messages: LangChain 消息列表、dict 列表或单个字符串

        Returns:
            AIMessage
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        formatted = self._format_messages(messages)
        params = self._build_params(formatted)
        response = completion(**params)
        return self._parse_response(response).to_langchain_message()

    # ========================================
    # 异步接口
    # ========================================

    async def ainvoke(self, messages: Union[list[BaseMessage], list[dict], str]) -> AIMessage:
        """
        异步调用

        Args:
            messages: LangChain 消息列表、dict 列表或单个字符串

        Returns:
            AIMessage
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        formatted = self._format_messages(messages)
        params = self._build_params(formatted)
        response = await acompletion(**params)
        return self._parse_response(response).to_langchain_message()

    async def astream(
        self,
        messages: Union[list[BaseMessage], list[dict], str]
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

        formatted = self._format_messages(messages)
        params = self._build_params(formatted, stream=True)

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
            - 预定义名称: "deepseek-chat", "qwen-plus" 等
            - LiteLLM 格式: "deepseek/deepseek-chat", "dashscope/qwen-turbo" 等
            - 自定义模型: 配合 base_url 使用
        temperature: 温度参数
        max_tokens: 最大 token 数
        streaming: 是否流式输出（预留，实际由调用方法决定）
        base_url: OpenAI 兼容接口地址（用于 Ollama/vLLM 等）
        api_key: API 密钥（自部署服务可能需要）
        **kwargs: 其他参数

    Returns:
        UnifiedLLM 实例

    Examples:
        # 使用预定义模型
        llm = create_llm("deepseek-chat")

        # 使用 Ollama 本地模型
        llm = create_llm(
            model="llama3",
            base_url="http://localhost:11434/v1",
            api_key="ollama"
        )

        # 使用 vLLM 部署的模型
        llm = create_llm(
            model="Qwen/Qwen2-7B-Instruct",
            base_url="http://localhost:8000/v1",
            api_key="token-abc123"
        )
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
            "support_reasoning": config.get("support_reasoning", False),
            "description": config.get("description", ""),
        }
    return {"model_id": model, "support_reasoning": False, "description": "Custom model"}


# ========================================
# 测试入口
# ========================================

if __name__ == "__main__":
    import asyncio

    async def test_basic():
        """基本功能测试"""
        print("\n" + "=" * 60)
        print("Testing basic invoke...")
        print("=" * 60)

        llm = create_llm("deepseek-chat", temperature=0.3)
        response = llm.invoke("Say 'Hello LiteLLM!' in exactly 3 words")

        print(f"Content: {response.content}")
        print(f"Token usage: {response.response_metadata.get('token_usage', {})}")

    async def test_stream():
        """流式输出测试"""
        print("\n" + "=" * 60)
        print("Testing async stream...")
        print("=" * 60)

        llm = create_llm("deepseek-chat", temperature=0.3)

        print("Streaming: ", end="", flush=True)
        async for chunk in llm.astream("Count from 1 to 5"):
            if chunk["type"] == "content":
                print(chunk["content"], end="", flush=True)
            elif chunk["type"] == "usage":
                print(f"\nToken usage: {chunk['token_usage']}")
        print()

    # 运行测试
    asyncio.run(test_basic())
    asyncio.run(test_stream())
