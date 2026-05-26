"""inspect_tool_failures.py — 下钻:晒出失败 tool_call 的原始形态

回答"Missing required parameter 到底是模型漏填、还是 XML 畸形让 parser 抽不出"。
对每个匹配的失败 tool_complete,打印:
  - error(校验报错,含 Received: 即 parser 实际抽到的参数)
  - parsed params(parser 抽出来的 params dict)
  - parser_warnings(畸形修复痕迹 —— 非空 = XML 被修过)
  - 触发它的那条 llm_complete 原文里对应的 <tool_call> 块(模型真实生成的 XML)
并给一个汇总:有 parser_warnings(畸形) vs 无(疑似真漏)的次数。

判别:
  parser_warnings 非空            → XML 畸形,修复时可能丢了 content → parser 侧问题
  parser_warnings 空 + params 无 content → 解析干净但模型真没写       → 模型漏填

数据访问复用 app 的 async ORM(asyncpg / aiomysql / aiosqlite),**不依赖 pandas**,
可在 backend 容器里直接跑。

用法:
    python scripts/inspect_tool_failures.py --tool create_artifact \
        --error-contains "Missing required parameter" --hours 720 --limit 3
    python scripts/inspect_tool_failures.py --tool update_artifact \
        --error-contains "new_str" --hours 168 --limit 2
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from db.models import MessageEvent


def _resolve_engine_url() -> str:
    """对齐 config.effective_database_url:DATABASE_URLS 优先,DATABASE_URL 兜底。"""
    urls = os.getenv("ARTIFACTFLOW_DATABASE_URLS", "")
    if urls:
        first = urls.split(",")[0].strip()
        if first:
            return first
    return os.getenv("ARTIFACTFLOW_DATABASE_URL", "") or "sqlite+aiosqlite:///data/artifactflow.db"


def _extract_tool_block(content: str, tool_name: str) -> str | None:
    """从 llm_complete 原文里抽出 <name>tool_name</name> 的那个 <tool_call> 块。

    末尾未闭合(漏 </tool_call>,小模型常见)也兜一把。
    """
    if not content:
        return None
    for m in re.finditer(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL | re.IGNORECASE):
        nm = re.search(r"<name>\s*(\w+)\s*</name>", m.group(1))
        if nm and nm.group(1) == tool_name:
            return m.group(0)
    opens = list(re.finditer(r"<tool_call>", content, re.IGNORECASE))
    if opens:
        tail = content[opens[-1].start():]
        nm = re.search(r"<name>\s*(\w+)\s*</name>", tail)
        if nm and nm.group(1) == tool_name:
            return tail
    return None


def _short(v, n: int = 100) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def _head_tail(s: str, n: int, head_frac: float = 0.6) -> str:
    """留头留尾、挖掉中段 —— 头部看模型填了哪些参数,尾部看 XML 是否正常收尾
    (闭合标签齐不齐;输出撞 4096 截断时尾部会缺 </content>/</tool_call> 或半截 CDATA)。"""
    if len(s) <= n:
        return s
    head_n = int(n * head_frac)
    tail_n = n - head_n
    marker = f"…(中间省略 {len(s) - n} 字 —— 留头留尾,重点看下方尾部 XML 是否正常闭合)…"
    return s[:head_n] + "\n" + marker + "\n" + s[-tail_n:]


async def _run(args) -> None:
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=args.hours)
    engine = create_async_engine(_resolve_engine_url())

    print(f"=== inspect tool failures: tool={args.tool}  error~='{args.error_contains}'  ({args.hours}h) ===\n")

    try:
        # 1) 拉窗口内全部 tool_complete(payload 小);在 Python 侧按 JSON 字段过滤。
        tc_stmt = (
            select(MessageEvent)
            .where(
                MessageEvent.event_type == "tool_complete",
                MessageEvent.created_at > threshold,
            )
            .order_by(MessageEvent.id)
        )
        async with AsyncSession(engine) as session:
            tc_rows = (await session.execute(tc_stmt)).scalars().all()

            matches = []
            for row in tc_rows:
                d = row.data or {}
                if d.get("tool") != args.tool or d.get("success"):
                    continue
                if args.error_contains not in (d.get("error") or ""):
                    continue
                matches.append(row)

            warn_yes = sum(1 for r in matches if (r.data or {}).get("parser_warnings"))
            warn_no = len(matches) - warn_yes

            # 2) 对前 limit 条,定点回捞触发它的 llm_complete 原文(只查 limit 次,内存有界)。
            for i, row in enumerate(matches[: args.limit], 1):
                d = row.data or {}
                warns = d.get("parser_warnings") or []
                params = d.get("params") or {}
                print(f"--- example #{i}  (event id={row.id}, agent={row.agent_name}, {row.created_at}) ---")
                print(f"  error         : {_short(d.get('error'), 240)}")
                print(f"  parsed params : { {k: _short(v, 80) for k, v in params.items()} }")
                print(f"  parser_warns  : {warns if warns else '(none)'}")

                llm_stmt = (
                    select(MessageEvent)
                    .where(
                        MessageEvent.event_type == "llm_complete",
                        MessageEvent.message_id == row.message_id,
                        MessageEvent.agent_name == row.agent_name,
                        MessageEvent.id < row.id,
                    )
                    .order_by(MessageEvent.id.desc())
                    .limit(1)
                )
                llm_row = (await session.execute(llm_stmt)).scalars().first()
                raw = _extract_tool_block((llm_row.data or {}).get("content") or "", args.tool) if llm_row else None
                if raw:
                    print(f"  --- raw <tool_call> the model actually generated (len={len(raw)}) ---")
                    for line in _head_tail(raw, args.max_chars).splitlines():
                        print("    " + line)
                else:
                    print("  (raw tool_call 无法从前序 llm_complete 回捞)")
                print()
    finally:
        await engine.dispose()

    print(
        f"=== summary: {args.tool} 匹配 '{args.error_contains}' 的失败共 {warn_yes + warn_no} 条 —— "
        f"有 parser_warnings(XML 畸形/修过)={warn_yes},无(解析干净→疑似真漏)={warn_no} ==="
    )


def main():
    p = argparse.ArgumentParser(description="Dump raw form of failed tool calls")
    p.add_argument("--tool", required=True, help="tool name, e.g. create_artifact")
    p.add_argument("--error-contains", default="Missing required parameter", help="substring filter on error")
    p.add_argument("--hours", type=int, default=720, help="lookback window in hours")
    p.add_argument("--limit", type=int, default=3, help="how many raw examples to print")
    p.add_argument("--max-chars", type=int, default=2500, help="raw block 字符预算(留头留尾,中段省略)")
    asyncio.run(_run(p.parse_args()))


if __name__ == "__main__":
    main()
