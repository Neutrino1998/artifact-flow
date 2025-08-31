"""
统一的LLM接口封装
基于LangChain实现，支持多种模型提供商
"""

import os
from typing import Optional, Dict, Any, Union
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI

# 尝试导入社区模型（可选依赖）
try:
    from langchain_community.chat_models.tongyi import ChatTongyi
    TONGYI_AVAILABLE = True
except ImportError:
    TONGYI_AVAILABLE = False
    ChatTongyi = None

try:
    from langchain_deepseek import ChatDeepSeek
    DEEPSEEK_AVAILABLE = True
except ImportError:
    DEEPSEEK_AVAILABLE = False
    ChatDeepSeek = None


from utils.logger import get_logger

# 加载环境变量
load_dotenv()

logger = get_logger("Models")


# 预定义模型配置
MODEL_CONFIGS = {
    # OpenAI
    "gpt-4o": {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": os.getenv("OPENAI_API_KEY"),
    },
    "gpt-4o-mini": {
        "provider": "openai",
        "model": "gpt-4o-mini", 
        "api_key": os.getenv("OPENAI_API_KEY"),
    },
    
    # Qwen (通义千问) - 使用ChatTongyi
    "qwen-flash": {
        "provider": "dashscope",
        "model": "qwen-flash",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
    },
    "qwen-plus": {
        "provider": "dashscope",
        "model": "qwen-plus",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
    },
    
    # Qwen3-30B 系列模型
    "qwen3-30b-thinking": {
        "provider": "dashscope",  # ChatTongyi应该能处理thinking模型
        "model": "qwen3-30b-a3b-thinking-2507",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "support_reasoning": True,
        "description": "Qwen3-30B思考模型，支持深度推理"
    },
    "qwen3-30b-instruct": {
        "provider": "dashscope",
        "model": "qwen3-30b-a3b-instruct-2507",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "description": "Qwen3-30B指令模型，快速响应"
    },
    
    # DeepSeek
    "deepseek-chat": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
    },
    "deepseek-reasoner": {
        "provider": "deepseek",
        "model": "deepseek-reasoner",
        "api_key": os.getenv("DEEPSEEK_API_KEY"),
        "support_reasoning": True,
        "description": "DeepSeek推理模型"
    },
}


def create_llm(
    model: str = "gpt-4o-mini",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    streaming: bool = False,
    **kwargs
):
    """
    创建LLM实例
    
    Args:
        model: 模型名称
        temperature: 温度参数
        max_tokens: 最大token数
        streaming: 是否流式输出
        **kwargs: 其他参数
    
    Returns:
        ChatModel实例 (ChatOpenAI/ChatTongyi/ChatDeepSeek)
    
    Example:
        # 使用预定义模型
        llm = create_llm("qwen-plus")
        response = llm.invoke("Hello!")
        print(response.content)  # 标准回答
        
        # 使用思考模型
        llm = create_llm("qwen3-30b-thinking")
        response = llm.invoke("解释量子纠缠")
        # 如果ChatTongyi支持，reasoning_content会在response的属性中
        if hasattr(response, 'reasoning_content'):
            print(response.reasoning_content)  # 思考过程
        print(response.content)  # 最终答案
    """
    
    # 获取配置
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model].copy()
        provider = config.pop("provider")
        model_name = config.pop("model")
        support_reasoning = config.pop("support_reasoning", False)
        config.pop("description", None)
        
        # 合并用户参数
        config.update(kwargs)
        
        # 根据provider创建对应的模型
        if provider == "openai":
            llm = ChatOpenAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                **config
            )
            logger.info(f"Created ChatOpenAI: {model_name}")
        
        elif provider == "dashscope" and TONGYI_AVAILABLE:
                llm = ChatTongyi(
                    model=model_name,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    streaming=streaming,
                    **config
                )
                logger.info(f"Created ChatTongyi: {model_name}")
 
        elif provider == "deepseek" and DEEPSEEK_AVAILABLE:
            llm = ChatDeepSeek(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                **config
            )
            logger.info(f"Created ChatDeepSeek: {model_name}")
        
        else:
            # 降级到ChatOpenAI（通用OpenAI兼容接口）
            logger.warning(f"Provider {provider} not available, using ChatOpenAI fallback")
            
            # 设置base_url
            if provider == "tongyi":
                config["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            elif provider == "deepseek":
                config["base_url"] = "https://api.deepseek.com/v1"
            
            llm = ChatOpenAI(
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
                **config
            )
            logger.info(f"Created ChatOpenAI (fallback): {model_name}")
        
        return llm
    
    else:
        # 用户自定义模型，使用ChatOpenAI
        llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=streaming,
            **kwargs
        )
        logger.info(f"Created custom LLM: {model}")
        return llm


def get_available_models() -> list[str]:
    """获取所有预定义的模型名称"""
    return list(MODEL_CONFIGS.keys())


def get_model_info(model: str) -> Dict[str, Any]:
    """获取模型信息"""
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model].copy()
        return {
            "model_id": config["model"],
            "provider": config["provider"],
            "description": config.get("description", ""),
        }
    return {"model_id": model, "provider": "unknown", "description": ""}


if __name__ == "__main__":
    # 检查可用的提供商
    print("\n📦 检查可用的提供商:")
    print(f"  - ChatTongyi: {'✅' if TONGYI_AVAILABLE else '❌ (需要安装 dashscope 和 langchain-community)'}")
    print(f"  - ChatDeepSeek: {'✅' if DEEPSEEK_AVAILABLE else '❌ (需要安装 langchain-deepseek)'}")
    
    # 测试问题
    test_question = "一个圆的半径是5，另一个圆的半径是3，如果这两个圆外切，求它们圆心之间的距离。"
    
    # 测试模型
    test_models = ["qwen3-30b-thinking", "qwen3-30b-instruct", "deepseek-chat", "deepseek-reasoner"]
    
    for model_name in test_models:
        print("=" * 60)
        try:
            # 创建模型并测试
            llm = create_llm(model_name, temperature=0.3)
            response = llm.invoke(test_question)
            
            print(f"📝 问题: {test_question}")
            print("-"*60)
            if 'reasoning_content' in response.additional_kwargs:
                print("💭 思考:", response.additional_kwargs.get('reasoning_content', ''))
                print("-"*60)
            print(f"💬 回答: {response.content}")
            print(response)
        except Exception as e:
            print(f"❌ 调用失败: {str(e)}")