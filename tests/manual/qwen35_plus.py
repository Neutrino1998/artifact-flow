"""
测试 Qwen3.5 系列的推理模式和非推理模式

通过 models.yaml 中的预定义配置，每个模型有 thinking / no-thinking 两种：
- "qwen3.5-plus"               → 思考模式
- "qwen3.5-plus-no-thinking"   → 非思考模式
- "qwen3.5-flash"              → 思考模式
- "qwen3.5-flash-no-thinking"  → 非思考模式

运行方式：
    python -m tests.manual.qwen35_plus
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_model_info


QUESTION = "If a train travels 60 km in 1 hour, how far will it travel in 2.5 hours? Think step by step."


async def test_model(model_name: str, expect_reasoning: bool):
    """测试指定模型"""
    print(f"\n{'=' * 60}")
    print(f"Testing: {model_name}")
    print(f"Expect reasoning: {expect_reasoning}")
    print(f"Model info: {get_model_info(model_name)}")
    print(f"{'=' * 60}")

    messages = [{"role": "user", "content": QUESTION}]

    try:
        reasoning_chunks = 0
        content_chunks = 0
        final_content = None
        final_reasoning = None

        async for chunk in astream_with_retry(messages, model=model_name):
            if chunk["type"] == "reasoning":
                reasoning_chunks += 1
            elif chunk["type"] == "content":
                content_chunks += 1
            elif chunk["type"] == "final":
                final_content = chunk["content"]
                final_reasoning = chunk["reasoning_content"]

        print(f"Reasoning chunks: {reasoning_chunks}")
        print(f"Content chunks: {content_chunks}")

        if final_content:
            preview = final_content[:200]
            print(f"Content: {preview}{'...' if len(final_content) > 200 else ''}")
        if final_reasoning:
            print(f"Reasoning: FOUND ({len(final_reasoning)} chars)")
        else:
            print(f"Reasoning: None")

        if expect_reasoning and reasoning_chunks > 0:
            print("Result: PASS")
        elif not expect_reasoning and reasoning_chunks == 0:
            print("Result: PASS")
        elif expect_reasoning and reasoning_chunks == 0:
            print("Result: FAIL (expected reasoning chunks but got 0)")
        else:
            print("Result: WARN (unexpected reasoning chunks)")
    except Exception as e:
        print(f"Result: ERROR {e}")


async def main():
    print("=" * 60)
    print("Qwen3.5 models.yaml 测试")
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
