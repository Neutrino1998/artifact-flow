"""inspect_tool_failures.py — inspect failed native tool calls.

对每个匹配的失败 tool_complete,打印:
  - error 与运行时实际收到的业务参数
  - call_id 绑定的标准 native tool-call wire object

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
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from db.models import MessageEvent
from utils.time import utc_now


def _resolve_engine_url() -> str:
    """对齐 config.effective_database_url:DATABASE_URLS 优先,DATABASE_URL 兜底。"""
    urls = os.getenv("ARTIFACTFLOW_DATABASE_URLS", "")
    if urls:
        first = urls.split(",")[0].strip()
        if first:
            return first
    return os.getenv("ARTIFACTFLOW_DATABASE_URL", "") or "sqlite+aiosqlite:///data/artifactflow.db"


def _extract_tool_call(
    llm_data: dict, call_id: str | None, tool_name: str
) -> dict | None:
    """Find the accepted native call by stable call id, with name fallback."""
    calls = llm_data.get("tool_calls") or []
    if call_id:
        for call in calls:
            if call.get("id") == call_id:
                return call
    for call in calls:
        if (call.get("function") or {}).get("name") == tool_name:
            return call
    return None


def _short(v, n: int = 100) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def _head_tail(s: str, n: int, head_frac: float = 0.6) -> str:
    """Keep both ends while bounding a potentially large JSON argument string."""
    if len(s) <= n:
        return s
    head_n = int(n * head_frac)
    tail_n = n - head_n
    marker = f"…(中间省略 {len(s) - n} 字)…"
    return s[:head_n] + "\n" + marker + "\n" + s[-tail_n:]


async def _run(args) -> None:
    threshold = utc_now() - timedelta(hours=args.hours)
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

            # 2) 对前 limit 条,定点回捞触发它的 llm_complete 原文(只查 limit 次,内存有界)。
            for i, row in enumerate(matches[: args.limit], 1):
                d = row.data or {}
                params = d.get("params") or {}
                print(f"--- example #{i}  (event id={row.id}, agent={row.agent_name}, {row.created_at}) ---")
                print(f"  call_id       : {d.get('call_id') or '(missing)'}")
                print(f"  error         : {_short(d.get('error'), 240)}")
                print(f"  runtime params: { {k: _short(v, 80) for k, v in params.items()} }")

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
                raw = _extract_tool_call(
                    llm_row.data or {}, d.get("call_id"), args.tool
                ) if llm_row else None
                if raw:
                    raw_json = json.dumps(raw, ensure_ascii=False, indent=2)
                    print(f"  --- accepted native tool call (len={len(raw_json)}) ---")
                    for line in _head_tail(raw_json, args.max_chars).splitlines():
                        print("    " + line)
                else:
                    print("  (native tool call 无法从前序 llm_complete 回捞)")
                print()
    finally:
        await engine.dispose()

    print(
        f"=== summary: {args.tool} 匹配 '{args.error_contains}' 的失败共 {len(matches)} 条 ==="
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
