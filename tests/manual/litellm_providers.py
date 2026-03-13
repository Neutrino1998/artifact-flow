"""
LiteLLM Provider 兼容性测试

测试各 provider 的：
1. 流式输出
2. Token usage 获取
3. Reasoning content 获取（针对推理模型）

运行方式：
    python -m tests.manual.litellm_providers
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_available_models


# ========================================
# 测试配置
# ========================================

# 基础测试模型（用于验证 token usage）
BASIC_MODELS = [
    "deepseek-chat",
    "qwen3.5-flash-no-thinking",
]

# 推理模型（用于验证 reasoning content）
REASONING_MODELS = [
    "deepseek-reasoner",
    "qwen3.5-plus",
]

# 测试问题
BASIC_QUESTION = "What is 2+2? Answer in one word."
REASONING_QUESTION = "If a train travels 60 km in 1 hour, how far will it travel in 2.5 hours? Think step by step."


# ========================================
# 测试函数
# ========================================

async def test_stream(model_name: str, question: str = BASIC_QUESTION, expect_reasoning: bool = False) -> dict:
    """测试流式输出"""
    print(f"\n{'-'*60}")
    print(f"Testing STREAM: {model_name} (reasoning={expect_reasoning})")
    print(f"{'-'*60}")

    result = {
        "model": model_name,
        "test": "reasoning_stream" if expect_reasoning else "stream",
        "success": False,
        "content": None,
        "reasoning_content": None,
        "token_usage": None,
        "content_chunks": 0,
        "reasoning_chunks": 0,
        "error": None,
    }

    try:
        messages = [{"role": "user", "content": question}]

        print("Streaming: ", end="", flush=True)
        async for chunk in astream_with_retry(messages, model=model_name):
            if chunk["type"] == "content":
                print(chunk["content"], end="", flush=True)
                result["content_chunks"] += 1
            elif chunk["type"] == "reasoning":
                result["reasoning_chunks"] += 1
                if result["reasoning_chunks"] <= 3:
                    print(f"\n  [reasoning] {chunk['content'][:50]}...", end="", flush=True)
            elif chunk["type"] == "usage":
                result["token_usage"] = chunk["token_usage"]
            elif chunk["type"] == "final":
                result["content"] = chunk["content"]
                result["reasoning_content"] = chunk["reasoning_content"]

        print()
        print(f"Content chunks: {result['content_chunks']}")
        print(f"Reasoning chunks: {result['reasoning_chunks']}")
        print(f"Token usage: {result['token_usage']}")

        if expect_reasoning:
            if result["reasoning_content"]:
                print(f"Reasoning content: FOUND ({len(result['reasoning_content'])} chars)")
            else:
                print("Reasoning content: NOT FOUND")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\nERROR: {e}")

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
    print("# PART 1: Basic Models (stream + token usage)")
    print("=" * 60)

    for model in BASIC_MODELS:
        result = await test_stream(model)
        all_results.append(result)

    # 2. 推理模型测试
    print("\n\n" + "=" * 60)
    print("# PART 2: Reasoning Models (stream + reasoning content)")
    print("=" * 60)

    for model in REASONING_MODELS:
        result = await test_stream(model, question=REASONING_QUESTION, expect_reasoning=True)
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

    # 4. Token usage 检查
    print("\n" + "-" * 60)
    print("Token Usage Check:")
    print("-" * 60)

    for r in all_results:
        if r["token_usage"]:
            has_prompt = "prompt_tokens" in r["token_usage"]
            has_completion = "completion_tokens" in r["token_usage"]
            ok = has_prompt and has_completion
            print(f"  {r['model']} ({r['test']}): {'OK' if ok else 'UNEXPECTED'} - {r['token_usage']}")

    # 5. Reasoning content 检查
    print("\n" + "-" * 60)
    print("Reasoning Content Check:")
    print("-" * 60)

    reasoning_results = [r for r in all_results if r["test"] == "reasoning_stream"]
    for r in reasoning_results:
        has_reasoning = r.get("reasoning_content") is not None
        print(f"  {r['model']}: {'FOUND' if has_reasoning else 'NOT FOUND'}")


if __name__ == "__main__":
    asyncio.run(main())
