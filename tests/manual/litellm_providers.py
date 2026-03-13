"""
LiteLLM Provider 兼容性测试

测试所有预定义模型的：
1. 流式输出
2. Token usage
3. Reasoning content（推理模型）

运行方式：
    python -m tests.manual.litellm_providers
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_available_models


# ========================================
# 配置
# ========================================

# 推理模型（期望有 reasoning content）
REASONING_MODELS = {
    "qwen3.5-plus", "qwen3.5-flash", "qwen3.5-35b-a3b",
    "deepseek-reasoner",
}

BASIC_QUESTION = "What is 2+2? Answer in one word."
REASONING_QUESTION = "If a train travels 60 km in 1 hour, how far will it travel in 2.5 hours? Think step by step."


# ========================================
# 核心测试
# ========================================

async def test_model(model_name: str) -> dict:
    """测试单个模型的流式输出"""
    expect_reasoning = model_name in REASONING_MODELS
    question = REASONING_QUESTION if expect_reasoning else BASIC_QUESTION

    result = {
        "model": model_name,
        "expect_reasoning": expect_reasoning,
        "success": False,
        "content": None,
        "reasoning_content": None,
        "token_usage": None,
        "content_chunks": 0,
        "reasoning_chunks": 0,
        "error": None,
    }

    print(f"\n{'=' * 60}")
    print(f"  {model_name}")
    print(f"  expect_reasoning: {expect_reasoning}")
    print(f"{'=' * 60}")

    try:
        messages = [{"role": "user", "content": question}]
        in_reasoning = False

        async for chunk in astream_with_retry(messages, model=model_name):
            if chunk["type"] == "reasoning":
                if not in_reasoning:
                    print("[Reasoning] ", end="", flush=True)
                    in_reasoning = True
                print(chunk["content"], end="", flush=True)
                result["reasoning_chunks"] += 1

            elif chunk["type"] == "content":
                if in_reasoning:
                    print("\n[Content]  ", end="", flush=True)
                    in_reasoning = False
                print(chunk["content"], end="", flush=True)
                result["content_chunks"] += 1

            elif chunk["type"] == "usage":
                result["token_usage"] = chunk["token_usage"]

            elif chunk["type"] == "final":
                result["content"] = chunk["content"]
                result["reasoning_content"] = chunk["reasoning_content"]

        print()

        print(f"\n{'-' * 40}")
        print(f"  content_chunks:   {result['content_chunks']}")
        print(f"  reasoning_chunks: {result['reasoning_chunks']}")
        print(f"  token_usage:      {result['token_usage']}")

        if expect_reasoning:
            has = result["reasoning_content"] is not None
            print(f"  reasoning:        {'FOUND (' + str(len(result['reasoning_content'])) + ' chars)' if has else 'NOT FOUND'}")

        result["success"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"\n  ERROR: {e}")

    print(f"{'-' * 40}")
    return result


# ========================================
# 主函数
# ========================================

async def main():
    models = get_available_models()

    print(f"\n{'=' * 60}")
    print(f"  LiteLLM Provider Compatibility Test")
    print(f"  Models: {len(models)}")
    print(f"{'=' * 60}")

    all_results = []
    for model in models:
        result = await test_model(model)
        all_results.append(result)

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}")

    success_count = sum(1 for r in all_results if r["success"])
    print(f"\n  {success_count}/{len(all_results)} passed\n")

    for r in all_results:
        status = "PASS" if r["success"] else "FAIL"
        extra = ""
        if r["error"]:
            extra = f"  ({r['error'][:60]})"
        elif r["expect_reasoning"]:
            has = r.get("reasoning_content") is not None
            extra = f"  reasoning={'yes' if has else 'NO'}"
        print(f"  [{status}] {r['model']}{extra}")

    # Token usage
    print(f"\n{'-' * 40}")
    print(f"  Token Usage")
    print(f"{'-' * 40}")
    for r in all_results:
        if r["token_usage"]:
            u = r["token_usage"]
            print(f"  {r['model']}: in={u.get('prompt_tokens', '?')} out={u.get('completion_tokens', '?')}")
        elif r["success"]:
            print(f"  {r['model']}: MISSING")

    print()


if __name__ == "__main__":
    asyncio.run(main())
