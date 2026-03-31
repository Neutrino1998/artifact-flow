"""
测试 llm_chunk coalescing 效果

让模型写一段 ~2000 字的内容，统计：
- 原始 chunk 数量和每个 chunk 的大小
- 80ms 合并后的 chunk 数量
- 总传输字节量对比
"""

import asyncio
import sys
import time
from pathlib import Path

# 项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models.llm import astream_with_retry


async def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5-plus"
    print(f"模型: {model}")
    print(f"合并窗口: 80ms\n")

    messages = [
        {"role": "user", "content": "请写一篇约2000字的文章，主题是分布式系统中的故障处理。直接输出文章内容。"}
    ]

    # 收集原始 chunks
    raw_chunks: list[dict] = []  # {"content": str, "time": float, "type": str}

    async for chunk in astream_with_retry(messages, model=model):
        if chunk["type"] in ("content", "reasoning"):
            raw_chunks.append({
                "content": chunk["content"],
                "time": time.monotonic(),
                "type": chunk["type"],
            })

    if not raw_chunks:
        print("没有收到任何 chunk")
        return

    # ── 按类型分组统计 ──
    reasoning_chunks = [c for c in raw_chunks if c["type"] == "reasoning"]
    content_chunks = [c for c in raw_chunks if c["type"] == "content"]

    print("=" * 50)
    print("原始 chunks（无合并）")
    print("=" * 50)

    for label, chunks in [("reasoning", reasoning_chunks), ("content", content_chunks)]:
        if not chunks:
            continue
        text = "".join(c["content"] for c in chunks)
        sizes = [len(c["content"]) for c in chunks]
        cumulative = 0
        acc = ""
        for c in chunks:
            acc += c["content"]
            cumulative += len(acc)
        print(f"\n  [{label}]")
        print(f"  总字符数: {len(text)}")
        print(f"  chunk 数: {len(chunks)}")
        print(f"  chunk 大小: min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)/len(sizes):.1f}")
        print(f"  累积快照总传输: {cumulative:,} 字符")

    total_chunks = len(raw_chunks)
    total_cumulative = 0
    acc = ""
    for c in raw_chunks:
        acc += c["content"]
        total_cumulative += len(acc)
    print(f"\n  总计: {total_chunks} chunks, 累积传输 {total_cumulative:,} 字符")

    # ── 模拟 80ms coalescing（按字段分开缓冲） ──
    WINDOW_MS = 80
    coalesced_chunks: list[dict] = []
    pending: dict[str, str] = {}  # type → 累积内容
    last_flush = raw_chunks[0]["time"]

    def flush():
        nonlocal last_flush
        for typ, content in pending.items():
            coalesced_chunks.append({"content": content, "type": typ})
        pending.clear()
        last_flush = time.monotonic()

    for c in raw_chunks:
        typ = c["type"]
        if typ not in pending:
            pending[typ] = ""
        pending[typ] += c["content"]

        # 非 chunk 事件前强制 flush（这里模拟 reasoning→content 切换）
        if typ == "content" and "reasoning" in pending:
            flush()
        elif (c["time"] - last_flush) >= WINDOW_MS / 1000:
            flush()

    if pending:
        flush()

    # ── 合并后统计 ──
    coalesced_reasoning = [c for c in coalesced_chunks if c["type"] == "reasoning"]
    coalesced_content = [c for c in coalesced_chunks if c["type"] == "content"]

    print()
    print("=" * 50)
    print("80ms coalescing 后")
    print("=" * 50)

    for label, chunks in [("reasoning", coalesced_reasoning), ("content", coalesced_content)]:
        if not chunks:
            continue
        sizes = [len(c["content"]) for c in chunks]
        cumulative = 0
        acc = ""
        for c in chunks:
            acc += c["content"]
            cumulative += len(acc)
        print(f"\n  [{label}]")
        print(f"  chunk 数: {len(chunks)}")
        print(f"  chunk 大小: min={min(sizes)}, max={max(sizes)}, avg={sum(sizes)/len(sizes):.1f}")
        print(f"  累积快照总传输: {cumulative:,} 字符")

    coalesced_total = len(coalesced_chunks)
    coalesced_cumulative = 0
    acc = ""
    for c in coalesced_chunks:
        acc += c["content"]
        coalesced_cumulative += len(acc)
    print(f"\n  总计: {coalesced_total} chunks, 累积传输 {coalesced_cumulative:,} 字符")

    # ── 对比 ──
    print()
    print("=" * 50)
    print("对比")
    print("=" * 50)
    reduction = (1 - coalesced_total / total_chunks) * 100
    bytes_reduction = (1 - coalesced_cumulative / total_cumulative) * 100
    print(f"chunk 数减少: {total_chunks} → {coalesced_total} ({reduction:.0f}%)")
    print(f"累积传输减少: {total_cumulative:,} → {coalesced_cumulative:,} ({bytes_reduction:.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
