"""
统一的LLM接口封装
基于LangChain实现，支持OpenAI接口兼容模型
"""

import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from ..utils.logger import get_logger

# 加载环境变量
load_dotenv()

logger = get_logger("LLM")


# 预定义模型配置
MODEL_CONFIGS = {
    # OpenAI
    "gpt-4o": {
        "model": "gpt-4o",
        "api_key": os.getenv("OPENAI_API_KEY"),
    },
    "gpt-4o-mini": {
        "model": "gpt-4o-mini", 
        "api_key": os.getenv("OPENAI_API_KEY"),
    },
    
    # Qwen (通义千问)
    "qwen-max": {
        "model": "qwen-max",
        "api_key": os.getenv("QWEN_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-plus": {
        "model": "qwen-plus",
        "api_key": os.getenv("QWEN_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-turbo": {
        "model": "qwen-turbo",
        "api_key": os.getenv("QWEN_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    
    # DeepSeek
    "deepseek-chat": {
        "model": "deepseek-chat",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "base_url": "https://api.deepseek.com/v1",
    },
    "deepseek-reasoner": {
        "model": "deepseek-reasoner",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "base_url": "https://api.deepseek.com/v1",
    },
}


def create_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    streaming: bool = False,
    **kwargs
) -> ChatOpenAI:
    """
    创建LLM实例
    
    Args:
        model: 模型名称，可以是预定义的名称或直接的模型ID
        temperature: 温度参数
        max_tokens: 最大token数
        streaming: 是否流式输出
        **kwargs: 其他ChatOpenAI支持的参数
    
    Returns:
        ChatOpenAI实例
    
    Example:
        # 使用预定义模型
        llm = create_llm("qwen-plus")
        response = llm.invoke("Hello!")
        
        # 使用自定义配置
        llm = create_llm(
            model="gpt-4",
            api_key="your-key",
            temperature=0.5
        )
        
        # 流式输出
        llm = create_llm("deepseek-chat", streaming=True)
        for chunk in llm.stream("Tell me a story"):
            print(chunk.content, end="")
    """
    # 如果是预定义模型，使用预定义配置
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model].copy()
        model_name = config.pop("model")
        
        # 合并用户提供的参数（用户参数优先）
        config.update(kwargs)
        
        llm = ChatOpenAI(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            **config
        )
        logger.info(f"Created LLM: {model_name}")
    else:
        # 直接使用用户提供的模型名称
        llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            **kwargs
        )
        logger.info(f"Created LLM: {model}")
    
    return llm


def get_available_models() -> list[str]:
    """获取所有预定义的模型名称"""
    return list(MODEL_CONFIGS.keys())


if __name__ == "__main__":
    # 测试代码
    import asyncio
    
    print("可用模型:", get_available_models())
    
    # 创建模型
    llm = create_llm("gpt-4o-mini", temperature=0.5)
    
    # 简单调用
    print("\n测试调用:")
    response = llm.invoke("What's 2+2?")
    print(f"Response: {response.content}")
    
    # 使用消息列表
    print("\n测试消息列表:")
    from langchain_core.messages import SystemMessage, HumanMessage
    
    messages = [
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="What's the capital of France?")
    ]
    response = llm.invoke(messages)
    print(f"Response: {response.content}")
    
    # 流式输出
    print("\n测试流式输出:")
    llm_stream = create_llm("gpt-4o-mini", streaming=True)
    for chunk in llm_stream.stream("Count from 1 to 5"):
        if chunk.content:
            print(chunk.content, end="", flush=True)
    print()
    
    # 异步调用
    async def test_async():
        print("\n测试异步调用:")
        response = await llm.ainvoke("What's the weather like?")
        print(f"Async Response: {response.content}")
    
    asyncio.run(test_async())