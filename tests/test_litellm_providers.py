"""
LiteLLM Provider 兼容性测试

测试各 provider 的：
1. 基本调用
2. Token usage 获取
3. Reasoning content 获取（针对推理模型）
4. 流式输出

运行方式：
    python -m tests.test_litellm_providers
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models.llm import create_llm, get_available_models


# ========================================
# 测试配置
# ========================================

# 基础测试模型（用于验证 token usage）
BASIC_MODELS = [
    "deepseek-chat",
    "qwen-plus",
]

# 推理模型（用于验证 reasoning content）
REASONING_MODELS = [
    "deepseek-reasoner",
    "qwen3-30b-thinking",  # 可选，取消注释以测试
]

# 测试问题
BASIC_QUESTION = "What is 2+2? Answer in one word."
REASONING_QUESTION = "If a train travels 60 km in 1 hour, how far will it travel in 2.5 hours? Think step by step."


# ========================================
# 测试函数
# ========================================

def test_basic_invoke(model_name: str) -> dict:
    """测试基本同步调用和 token usage"""
    print(f"\n{'-'*60}")
    print(f"Testing BASIC INVOKE: {model_name}")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "basic_invoke",
        "success": False,
        "content": None,
        "token_usage": None,
        "error": None,
    }

    try:
        llm = create_llm(model_name, temperature=0.1, max_tokens=100)
        response = llm.invoke(BASIC_QUESTION)

        result["content"] = response.content
        result["token_usage"] = response.response_metadata.get("token_usage", {})
        result["success"] = True

        print(f"Content: {response.content}")
        print(f"Token usage: {result['token_usage']}")

        # 验证 token usage 格式
        token_usage = result["token_usage"]
        if "input_tokens" in token_usage and "output_tokens" in token_usage:
            print("Token usage format: OK (input_tokens/output_tokens)")
        else:
            print(f"Token usage format: UNEXPECTED - {token_usage}")

    except Exception as e:
        result["error"] = str(e)
        print(f"ERROR: {e}")

    return result


async def test_async_invoke(model_name: str) -> dict:
    """测试异步调用"""
    print(f"\n{'-'*60}")
    print(f"Testing ASYNC INVOKE: {model_name}")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "async_invoke",
        "success": False,
        "content": None,
        "token_usage": None,
        "error": None,
    }

    try:
        llm = create_llm(model_name, temperature=0.1, max_tokens=100)
        response = await llm.ainvoke(BASIC_QUESTION)

        result["content"] = response.content
        result["token_usage"] = response.response_metadata.get("token_usage", {})
        result["success"] = True

        print(f"Content: {response.content}")
        print(f"Token usage: {result['token_usage']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"ERROR: {e}")

    return result


async def test_stream(model_name: str) -> dict:
    """测试流式输出"""
    print(f"\n{'-'*60}")
    print(f"Testing STREAM: {model_name}")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "stream",
        "success": False,
        "content": None,
        "token_usage": None,
        "chunks_received": 0,
        "error": None,
    }

    try:
        llm = create_llm(model_name, temperature=0.1, max_tokens=100)

        print("Streaming: ", end="", flush=True)
        async for chunk in llm.astream(BASIC_QUESTION):
            if chunk["type"] == "content":
                print(chunk["content"], end="", flush=True)
                result["chunks_received"] += 1
            elif chunk["type"] == "usage":
                result["token_usage"] = chunk["token_usage"]
            elif chunk["type"] == "final":
                result["content"] = chunk["content"]

        print()
        print(f"Chunks received: {result['chunks_received']}")
        print(f"Token usage: {result['token_usage']}")
        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\nERROR: {e}")

    return result


async def test_reasoning(model_name: str) -> dict:
    """测试推理模型的 reasoning content"""
    print(f"\n{'-'*60}")
    print(f"Testing REASONING: {model_name}")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "reasoning",
        "success": False,
        "content": None,
        "reasoning_content": None,
        "token_usage": None,
        "error": None,
    }

    try:
        llm = create_llm(model_name, temperature=0.1, max_tokens=1000)
        response = await llm.ainvoke(REASONING_QUESTION)

        result["content"] = response.content
        result["reasoning_content"] = response.additional_kwargs.get("reasoning_content")
        result["token_usage"] = response.response_metadata.get("token_usage", {})
        result["success"] = True

        print(f"Content: {response.content[:200]}..." if len(response.content) > 200 else f"Content: {response.content}")

        if result["reasoning_content"]:
            reasoning_preview = result["reasoning_content"][:300]
            print(f"Reasoning content: {reasoning_preview}..." if len(result["reasoning_content"]) > 300 else f"Reasoning content: {result['reasoning_content']}")
        else:
            print("Reasoning content: NOT FOUND")

        print(f"Token usage: {result['token_usage']}")

    except Exception as e:
        result["error"] = str(e)
        print(f"ERROR: {e}")

    return result


async def test_reasoning_stream(model_name: str) -> dict:
    """测试推理模型的流式 reasoning content"""
    print(f"\n{'-'*60}")
    print(f"Testing REASONING STREAM: {model_name}")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "reasoning_stream",
        "success": False,
        "content": None,
        "reasoning_content": None,
        "token_usage": None,
        "reasoning_chunks": 0,
        "content_chunks": 0,
        "error": None,
    }

    try:
        llm = create_llm(model_name, temperature=0.1, max_tokens=1000)

        reasoning_parts = []
        content_parts = []

        print("Streaming reasoning model...")
        async for chunk in llm.astream(REASONING_QUESTION):
            if chunk["type"] == "reasoning":
                reasoning_parts.append(chunk["content"])
                result["reasoning_chunks"] += 1
                # 打印前几个 reasoning chunks
                if result["reasoning_chunks"] <= 3:
                    print(f"  [reasoning] {chunk['content'][:50]}...")
            elif chunk["type"] == "content":
                content_parts.append(chunk["content"])
                result["content_chunks"] += 1
            elif chunk["type"] == "usage":
                result["token_usage"] = chunk["token_usage"]
            elif chunk["type"] == "final":
                result["content"] = chunk["content"]
                result["reasoning_content"] = chunk["reasoning_content"]

        print(f"\nReasoning chunks: {result['reasoning_chunks']}")
        print(f"Content chunks: {result['content_chunks']}")
        print(f"Token usage: {result['token_usage']}")

        if result["reasoning_content"]:
            print(f"Reasoning content length: {len(result['reasoning_content'])} chars")
        else:
            print("Reasoning content: NOT FOUND in stream")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"ERROR: {e}")

    return result


# ========================================
# 主函数
# ========================================

async def main():
    print("\n" + "=" * 60)
    print("LiteLLM Provider Compatibility Test")
    print("=" * 60)
    print(f"\nAvailable models: {get_available_models()}")

    all_results = []

    # 1. 基础模型测试
    print("\n\n" + "=" * 60)
    print("# PART 1: Basic Models")
    print("=" * 60)

    for model in BASIC_MODELS:
        # 同步调用
        result = test_basic_invoke(model)
        all_results.append(result)

        # 异步调用
        result = await test_async_invoke(model)
        all_results.append(result)

        # 流式输出
        result = await test_stream(model)
        all_results.append(result)

    # 2. 推理模型测试
    print("\n\n" + "=" * 60)
    print("# PART 2: Reasoning Models")
    print("=" * 60)

    for model in REASONING_MODELS:
        # 非流式推理
        result = await test_reasoning(model)
        all_results.append(result)

        # 流式推理
        result = await test_reasoning_stream(model)
        all_results.append(result)

    # 3. 汇总结果
    print("\n\n" + "-" * 60)
    print("SUMMARY")
    print("-" * 60)

    success_count = sum(1 for r in all_results if r["success"])
    total_count = len(all_results)

    print(f"\nTotal: {success_count}/{total_count} tests passed\n")

    for r in all_results:
        status = "PASS" if r["success"] else "FAIL"
        print(f"  [{status}] {r['model']} - {r['test']}")
        if r["error"]:
            print(f"         Error: {r['error'][:80]}")

    # 4. Token usage 格式检查
    print("\n" + "-" * 60)
    print("Token Usage Format Check:")
    print("-" * 60)

    for r in all_results:
        if r["token_usage"]:
            has_input = "input_tokens" in r["token_usage"]
            has_output = "output_tokens" in r["token_usage"]
            format_ok = has_input and has_output
            print(f"  {r['model']} ({r['test']}): {'OK' if format_ok else 'UNEXPECTED'} - {r['token_usage']}")

    # 5. Reasoning content 检查
    print("\n" + "-" * 60)
    print("Reasoning Content Check:")
    print("-" * 60)

    reasoning_results = [r for r in all_results if r["test"] in ("reasoning", "reasoning_stream")]
    for r in reasoning_results:
        has_reasoning = r.get("reasoning_content") is not None
        print(f"  {r['model']} ({r['test']}): {'FOUND' if has_reasoning else 'NOT FOUND'}")


if __name__ == "__main__":
    asyncio.run(main())
