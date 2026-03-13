"""
LLM 交互式测试

从 models.yaml 加载所有模型，选择后进入对话模式。
适用于测试私有部署模型或调试特定模型。

运行方式：
    python -m tests.manual.llm_playground
    python -m tests.manual.llm_playground --model local-llama
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.llm import astream_with_retry, get_available_models, get_model_info


def select_model() -> str:
    """交互式选择模型"""
    models = get_available_models()

    print(f"\n{'=' * 60}")
    print(f"  Available Models")
    print(f"{'=' * 60}")

    for i, name in enumerate(models, 1):
        info = get_model_info(name)
        print(f"  {i:>2}. {name:<35} ({info['model_id']})")

    print(f"{'=' * 60}")

    while True:
        choice = input(f"\nSelect model [1-{len(models)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            return models[int(choice) - 1]
        print(f"  Invalid choice, enter 1-{len(models)}")


async def chat_loop(model_name: str):
    """单轮对话测试循环"""
    info = get_model_info(model_name)

    print(f"\n{'=' * 60}")
    print(f"  Model:    {model_name}")
    print(f"  Model ID: {info['model_id']}")
    print(f"  Commands: /quit to exit")
    print(f"{'=' * 60}")

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break

        messages = [{"role": "user", "content": user_input}]
        in_reasoning = False

        try:
            token_usage = None

            async for chunk in astream_with_retry(messages, model=model_name):
                if chunk["type"] == "reasoning":
                    if not in_reasoning:
                        print("[Reasoning] ", end="", flush=True)
                        in_reasoning = True
                    print(chunk["content"], end="", flush=True)

                elif chunk["type"] == "content":
                    if in_reasoning:
                        print("\n[Content]  ", end="", flush=True)
                        in_reasoning = False
                    print(chunk["content"], end="", flush=True)

                elif chunk["type"] == "usage":
                    token_usage = chunk["token_usage"]

            print()

            if token_usage:
                print(f"\n{'-' * 40}")
                print(f"  in={token_usage.get('prompt_tokens', '?')} out={token_usage.get('completion_tokens', '?')} total={token_usage.get('total_tokens', '?')}")
                print(f"{'-' * 40}")

        except Exception as e:
            print(f"\n  ERROR: {e}")

    print("\nBye.")


async def main():
    parser = argparse.ArgumentParser(description="LLM interactive playground")
    parser.add_argument("--model", "-m", help="Model name (skip selection)")
    args = parser.parse_args()

    if args.model:
        models = get_available_models()
        if args.model not in models:
            print(f"Unknown model '{args.model}'. Available: {models}")
            sys.exit(1)
        model_name = args.model
    else:
        model_name = select_model()

    await chat_loop(model_name)


if __name__ == "__main__":
    asyncio.run(main())
