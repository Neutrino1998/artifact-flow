"""
测试 Qwen3.5 系列的推理模式和非推理模式

通过 MODEL_CONFIGS 中的预定义配置，每个模型有 thinking / no-thinking 两种：
- "qwen3.5-plus"               → 思考模式
- "qwen3.5-plus-no-thinking"   → 非思考模式
- "qwen3.5-flash"              → 思考模式
- "qwen3.5-flash-no-thinking"  → 非思考模式

运行方式：
    python -m tests.manual.test_qwen35_plus
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import create_llm, get_model_info


QUESTION = "If a train travels 60 km in 1 hour, how far will it travel in 2.5 hours? Think step by step."


async def test_model(model_name: str, expect_reasoning: bool):
    """测试指定模型"""
    print(f"\n{'=' * 60}")
    print(f"Testing: {model_name}")
    print(f"Expect reasoning: {expect_reasoning}")
    print(f"Model info: {get_model_info(model_name)}")
    print(f"{'=' * 60}")

    llm = create_llm(model_name, temperature=0.1, max_tokens=2000)
    print(f"  model_name: {llm.model_name}")
    print(f"  extra_params: {llm.extra_params}")

    # --- 非流式测试 ---
    print(f"\n--- 非流式调用 ---")
    try:
        response = await llm.ainvoke(QUESTION)
        content = response.content
        reasoning = response.additional_kwargs.get("reasoning_content")
        token_usage = response.response_metadata.get("token_usage", {})

        print(f"Content: {content[:200]}{'...' if len(content) > 200 else ''}")
        if reasoning:
            print(f"Reasoning: FOUND ({len(reasoning)} chars)")
        else:
            print(f"Reasoning: None")
        print(f"Token usage: {token_usage}")

        if expect_reasoning and reasoning:
            print("Result: PASS ✅")
        elif not expect_reasoning and not reasoning:
            print("Result: PASS ✅")
        elif expect_reasoning and not reasoning:
            print("Result: FAIL ❌ (expected reasoning but not found)")
        else:
            print("Result: WARN ⚠️  (unexpected reasoning content)")
    except Exception as e:
        print(f"Result: ERROR ❌ {e}")

    # --- 流式测试 ---
    print(f"\n--- 流式调用 ---")
    try:
        reasoning_chunks = 0
        content_chunks = 0

        async for chunk in llm.astream(QUESTION):
            if chunk["type"] == "reasoning":
                reasoning_chunks += 1
            elif chunk["type"] == "content":
                content_chunks += 1

        print(f"Reasoning chunks: {reasoning_chunks}")
        print(f"Content chunks: {content_chunks}")

        if expect_reasoning and reasoning_chunks > 0:
            print("Result: PASS ✅")
        elif not expect_reasoning and reasoning_chunks == 0:
            print("Result: PASS ✅")
        elif expect_reasoning and reasoning_chunks == 0:
            print("Result: FAIL ❌ (expected reasoning chunks but got 0)")
        else:
            print("Result: WARN ⚠️  (unexpected reasoning chunks)")
    except Exception as e:
        print(f"Result: ERROR ❌ {e}")


async def main():
    print("=" * 60)
    print("Qwen3.5 MODEL_CONFIGS 测试")
    print("=" * 60)

    test_cases = [
        ("qwen3.5-plus", True),
        ("qwen3.5-plus-no-thinking", False),
        ("qwen3.5-flash", True),
        ("qwen3.5-flash-no-thinking", False),
    ]

    for model_name, expect_reasoning in test_cases:
        await test_model(model_name, expect_reasoning)

    print(f"\n{'=' * 60}")
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
