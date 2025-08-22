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
    
    # Qwen (通义千问) - 根据测试结果更新
    "qwen-max": {
        "model": "qwen-max",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-plus": {
        "model": "qwen-plus",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "qwen-turbo": {
        "model": "qwen-turbo",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    
    # Qwen3-30B 系列模型 (2507版本)
    "qwen3-30b-thinking": {
        "model": "qwen3-30b-a3b-thinking-2507",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "description": "Qwen3-30B思考模型，支持深度推理和逐步分析"
    },
    "qwen3-30b-instruct": {
        "model": "qwen3-30b-a3b-instruct-2507",
        "api_key": os.getenv("DASHSCOPE_API_KEY"),
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "description": "Qwen3-30B指令模型，快速直接回答"
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
        "description": "DeepSeek推理模型，支持复杂逻辑"
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
        
        # 使用思考模型
        llm = create_llm("qwen3-30b-thinking", temperature=0.1)
        response = llm.invoke("解释量子纠缠")
        
        # 流式输出
        llm = create_llm("deepseek-chat", streaming=True)
        for chunk in llm.stream("Tell me a story"):
            print(chunk.content, end="")
    """
    # 如果是预定义模型，使用预定义配置
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model].copy()
        model_name = config.pop("model")
        
        # 移除description字段（如果存在）
        config.pop("description", None)
        
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


def get_model_info(model: str) -> Dict[str, Any]:
    """获取模型信息"""
    if model in MODEL_CONFIGS:
        config = MODEL_CONFIGS[model].copy()
        return {
            "model_id": config["model"],
            "description": config.get("description", ""),
            "provider": "Qwen" if "qwen" in model else "OpenAI" if "gpt" in model else "DeepSeek"
        }
    return {"model_id": model, "description": "", "provider": "Unknown"}


if __name__ == "__main__":
    # 简化的测试代码
    print("🧪 LLM模块测试")
    print("=" * 40)
    
    # 测试问题 - 使用需要推理的数学题来对比两个30B模型
    test_question = "一个圆的半径是5，另一个圆的半径是3，如果这两个圆外切，求它们圆心之间的距离。"
    
    # 测试可用的模型 (包含两个30B模型对比)
    test_models = ["qwen-turbo", "qwen3-30b-thinking", "qwen3-30b-instruct"]
    
    for model_name in test_models:
        print(f"\n🤖 测试模型: {model_name}")
        print("-" * 30)
        
        try:
            # 检查API Key
            config = MODEL_CONFIGS.get(model_name, {})
            api_key = config.get("api_key")
            
            if not api_key:
                print(f"❌ 跳过: 未设置API Key")
                continue
            
            # 创建模型并测试
            llm = create_llm(model_name, temperature=0.3)
            response = llm.invoke(test_question)
            
            print(f"✅ 调用成功")
            print(f"📝 问题: {test_question}")
            print(f"💬 回答: \n{response.content}")
            
            print(f"📊 回答长度: {len(response.content)} 字符")
            
        except Exception as e:
            print(f"❌ 调用失败: {str(e)}")
    
    # 显示所有可用模型
    print(f"\n📋 预定义模型列表:")
    for model in get_available_models():
        info = get_model_info(model)
        print(f"   {model}: {info['model_id']} ({info['provider']})")
        if info['description']:
            print(f"      {info['description']}")
    
    print(f"\n✅ 测试完成")