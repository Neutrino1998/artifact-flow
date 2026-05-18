"""
observability_report.py — 跑一下就能看的 obs 报告

数据源:
  1. MessageEvent JSON 列 (业务侧:LLM / 工具调用)
  2. data/observability/metrics.jsonl* (运行时:loop_lag / RSS / DB pool / Redis)
  3. data/observability/loop-lag.jsonl* (事件:loop 软退化触发记录)

用法:
    python scripts/observability_report.py             # 24h 窗口
    python scripts/observability_report.py --hours 72  # 72h 窗口

DB 访问复用 app 的 async driver(asyncpg / aiomysql / aiosqlite)+ ORM
`select(MessageEvent)`,在 Python 侧拍平 `data` JSON 列到 DataFrame —— 不
写方言特化 SQL,也不需要 sync driver(psycopg2 / pymysql)。pandas 是分析
工具,不在 runtime requirements.txt;`pip install pandas` 或走 release
bundle 的离线 wheel 安装。

硬 wedge dump 看 docker logs backend 找 faulthandler 的 "Thread 0x..."
行,本脚本不聚合。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path

# Make src/ importable so we can reuse the ORM model.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

# Lazily set by main() — keeps helpers importable when pandas is absent.
pd = None  # type: ignore[assignment]


def _require_pandas():
    """Lazy import of pandas with friendly install hint on miss."""
    try:
        import pandas as pd
        return pd
    except ImportError:
        print(
            "ERROR: pandas not installed. Install with `pip install pandas` "
            "or via the release bundle's offline wheels "
            "(`pip install --no-index --find-links <bundle>/wheels pandas`).",
            file=sys.stderr,
        )
        sys.exit(2)


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from db.models import MessageEvent


def _resolve_engine_url() -> str:
    """对齐 config.effective_database_url 的优先级:DATABASE_URLS 优先,DATABASE_URL 兜底。

    生产同时设了二者时,主应用写的是 URLS 的第一个;脚本反过来读会查错库。

    URL 原样保留(含 +asyncpg / +aiomysql / +aiosqlite 后缀)—— script 走 ORM
    select(MessageEvent) + AsyncSession 读数据,复用 app 已有的 async driver,
    不需要 sync driver(psycopg2 / pymysql)。
    """
    url = ""
    urls = os.getenv("ARTIFACTFLOW_DATABASE_URLS", "")
    if urls:
        first = urls.split(",")[0].strip()
        if first:
            url = first
    if not url:
        url = os.getenv("ARTIFACTFLOW_DATABASE_URL", "")
    if not url:
        url = "sqlite+aiosqlite:///data/artifactflow.db"
    return url


def _llm_row_to_dict(row: MessageEvent) -> dict:
    data = row.data or {}
    usage = data.get("token_usage") or {}
    return {
        "created_at": row.created_at,
        "agent_name": row.agent_name,
        "model": data.get("model"),
        "in_tok": usage.get("input_tokens"),
        "out_tok": usage.get("output_tokens"),
        "dur_ms": data.get("duration_ms"),
    }


def _tool_row_to_dict(row: MessageEvent) -> dict:
    data = row.data or {}
    return {
        "created_at": row.created_at,
        "tool": data.get("tool"),
        "dur_ms": data.get("duration_ms"),
        "success": data.get("success"),
        # metadata 已经是 dict(ORM JSON 列自动 deserialize);后续 json_normalize 直接吃
        "metadata": data.get("metadata"),
    }


async def _load_message_events(async_engine, hours: int):
    """走 ORM select(MessageEvent) 拉事件,在 Python 侧拍平到 DataFrame。

    用 ORM 而非 raw SQL 的理由:`MessageEvent.data` 是 SQLAlchemy `JSON` 列,
    PG/MySQL/SQLite 三种方言的存取细节由类型适配器吸收(读出来一律是 Python
    dict),脚本里不需要再写方言分支。新增字段时也只动 _llm_row_to_dict /
    _tool_row_to_dict,无 SQL 漂移面。

    用 AsyncSession 而非 AsyncConnection:Connection.execute(select(Entity))
    返回 Core Row(列元组),.scalars() 只剪到第一列(id);要拿到 hydrate 后的
    ORM 实例必须走 Session。
    """
    # 事件 created_at 全链路 naive UTC(utils.time.utc_now,见 incident
    # 2026-05-14 PR-tz-unify):应用写 / DB server_default 都是 UTC naive
    # (SQLite 自动 UTC;PG 需 TIMEZONE=UTC 部署对齐)。threshold 同样取
    # naive UTC,两边对齐;tz-aware 在 SQLite 上会报 can't compare。
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)

    llm_stmt = (
        select(MessageEvent)
        .where(
            MessageEvent.event_type == "llm_complete",
            MessageEvent.created_at > threshold,
        )
        .order_by(MessageEvent.id)
    )
    tool_stmt = (
        select(MessageEvent)
        .where(
            MessageEvent.event_type == "tool_complete",
            MessageEvent.created_at > threshold,
        )
        .order_by(MessageEvent.id)
    )

    async with AsyncSession(async_engine) as session:
        llm_rows = (await session.execute(llm_stmt)).scalars().all()
        tool_rows = (await session.execute(tool_stmt)).scalars().all()

    df_llm = pd.DataFrame([_llm_row_to_dict(r) for r in llm_rows])
    df_tool = pd.DataFrame([_tool_row_to_dict(r) for r in tool_rows])
    return df_llm, df_tool


def _load_jsonl_glob(pattern: str) -> pd.DataFrame:
    """读所有切片(`.jsonl`, `.jsonl.1` ...),拼成一个 DF。"""
    files = sorted(glob(pattern))
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        try:
            frames.append(pd.read_json(f, lines=True))
        except (ValueError, FileNotFoundError):
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _print_llm_summary(df_llm: pd.DataFrame, hours: int) -> None:
    print(f"\n=== LLM calls ({hours}h, by model × agent) ===")
    if df_llm.empty:
        print("  (no data)")
        return
    g = df_llm.groupby(["model", "agent_name"]).agg(
        calls=("dur_ms", "count"),
        in_tok=("in_tok", "sum"),
        out_tok=("out_tok", "sum"),
        p50_ms=("dur_ms", lambda s: s.quantile(0.5)),
        p99_ms=("dur_ms", lambda s: s.quantile(0.99)),
    )
    print(g.to_string())


def _print_tool_summary(df_tool: pd.DataFrame, hours: int) -> None:
    print(f"\n=== Tool calls ({hours}h, by tool) ===")
    if df_tool.empty:
        print("  (no data)")
        return
    g = df_tool.groupby("tool").agg(
        calls=("dur_ms", "count"),
        p50_ms=("dur_ms", lambda s: s.quantile(0.5)),
        p99_ms=("dur_ms", lambda s: s.quantile(0.99)),
        max_ms=("dur_ms", "max"),
        failures=("success", lambda s: int((~s.astype(bool)).sum())),
    )
    print(g.to_string())


def _print_fuzzy_stats(df_tool: pd.DataFrame) -> None:
    """update_artifact fuzzy_stats 调参报表。"""
    print("\n=== update_artifact fuzzy_stats 调参报表 ===")
    if df_tool.empty:
        print("  (no data)")
        return

    sub = df_tool[df_tool["tool"] == "update_artifact"]
    metas = sub["metadata"].dropna().apply(
        lambda m: m.get("fuzzy_stats") if isinstance(m, dict) else None
    ).dropna()
    if metas.empty:
        print("  (no fuzzy_stats events)")
        return

    df_fuzzy = pd.json_normalize(metas)

    print("-- outcome distribution --")
    print(df_fuzzy["outcome"].value_counts().to_string())

    if "unique_centers" in df_fuzzy.columns:
        print("\n-- unique_centers histogram (vs MAX_UNIQUE_CENTERS) --")
        # 简单分箱
        bins = [0, 5, 10, 20, 30, 50, 100, 10000]
        print(
            pd.cut(df_fuzzy["unique_centers"], bins=bins, include_lowest=True)
            .value_counts()
            .sort_index()
            .to_string()
        )

    if "elapsed_ms" in df_fuzzy.columns:
        s = df_fuzzy["elapsed_ms"].dropna()
        if not s.empty:
            print("\n-- elapsed_ms vs MAX_FUZZY_WALL_CLOCK_MS --")
            print(
                f"  p50={s.quantile(0.5):.1f}  p99={s.quantile(0.99):.1f}  max={s.max():.1f}"
            )

    matched = df_fuzzy[df_fuzzy["outcome"] == "matched"]
    if not matched.empty and "similarity_pct" in matched.columns:
        print("\n-- similarity_pct histogram (matched only) --")
        bins = [0, 70, 80, 90, 95, 99, 100]
        print(
            pd.cut(matched["similarity_pct"], bins=bins, include_lowest=True)
            .value_counts()
            .sort_index()
            .to_string()
        )

    if "old_str_hash" in df_fuzzy.columns:
        print("\n-- top-10 most frequently triggered old_str hashes --")
        print(df_fuzzy["old_str_hash"].value_counts().head(10).to_string())


def _print_runtime_summary(df_runtime: pd.DataFrame) -> None:
    print("\n=== Runtime metrics (data/observability/metrics.jsonl*) ===")
    if df_runtime.empty:
        print("  (no data — sampler not enabled or not yet flushed)")
        return

    print(f"  rows={len(df_runtime)}, window=[{df_runtime['ts'].min()} ~ {df_runtime['ts'].max()}]")

    # loop_lag p50/p99/max
    if "loop_lag_ms" in df_runtime.columns:
        # loop_lag_ms 是 dict;normalize
        ll = pd.json_normalize(df_runtime["loop_lag_ms"].dropna())
        for col in ("p50_ms", "p99_ms", "max_1m_ms"):
            if col in ll.columns:
                s = ll[col].dropna()
                if not s.empty:
                    print(f"  loop_lag {col}: median={s.median():.1f}  p99={s.quantile(0.99):.1f}  max={s.max():.1f}")

    # process RSS/CPU/FD
    if "process" in df_runtime.columns:
        proc = pd.json_normalize(df_runtime["process"].dropna())
        for col, label in (("rss_mb", "RSS (MB)"), ("cpu_pct", "CPU %"), ("open_fds", "Open FDs")):
            if col in proc.columns:
                s = proc[col].dropna()
                if not s.empty:
                    print(f"  {label}: min={s.min():.1f}  p50={s.quantile(0.5):.1f}  p99={s.quantile(0.99):.1f}  max={s.max():.1f}")

    if "db_pool" in df_runtime.columns:
        pool = pd.json_normalize(df_runtime["db_pool"].dropna())
        for col in ("in_use", "overflow"):
            if col in pool.columns:
                s = pool[col].dropna()
                if not s.empty:
                    print(f"  db_pool.{col}: p99={s.quantile(0.99):.1f}  max={s.max():.1f}")


def _print_lag_events(df_lag: pd.DataFrame) -> None:
    print("\n=== Loop-lag events (软退化, data/observability/loop-lag.jsonl*) ===")
    if df_lag.empty:
        print("  (no events — loop_lag never exceeded threshold)")
        return
    print(f"  total events: {len(df_lag)}")
    if "lag_ms" in df_lag.columns:
        s = df_lag["lag_ms"].dropna()
        print(f"  lag_ms: p50={s.quantile(0.5):.0f}  p99={s.quantile(0.99):.0f}  max={s.max():.0f}")
    if "wedged" in df_lag.columns:
        w = int(df_lag["wedged"].astype(bool).sum())
        if w:
            print(f"  WEDGED events (no loop response within watchdog timeout): {w}")
    print("\n  -- last 5 events (truncated) --")
    last = df_lag.tail(5)
    for _, row in last.iterrows():
        tasks_count = len(row.get("tasks", [])) if isinstance(row.get("tasks"), list) else 0
        print(f"    {row.get('ts', '?')}  lag={row.get('lag_ms', '?')}ms  tasks={tasks_count}")

    print("\n  Hard wedge (GIL held by C extension) dump 入口:")
    print("    docker logs backend 2>&1 | grep -A 200 'Thread 0x'")


async def _run_report(hours: int, obs_dir: str) -> None:
    print(f"ArtifactFlow observability report (last {hours}h)")
    print("=" * 70)

    # MessageEvent
    async_engine = None
    try:
        async_engine = create_async_engine(_resolve_engine_url())
        df_llm, df_tool = await _load_message_events(async_engine, hours)
        _print_llm_summary(df_llm, hours)
        _print_tool_summary(df_tool, hours)
        _print_fuzzy_stats(df_tool)
    except Exception as e:
        print(f"\n[skip] MessageEvent: {e}", file=sys.stderr)
    finally:
        if async_engine is not None:
            await async_engine.dispose()

    # Runtime metrics
    metrics_glob = str(Path(obs_dir) / "metrics.jsonl*")
    df_runtime = _load_jsonl_glob(metrics_glob)
    _print_runtime_summary(df_runtime)

    # Loop-lag events
    lag_glob = str(Path(obs_dir) / "loop-lag.jsonl*")
    df_lag = _load_jsonl_glob(lag_glob)
    _print_lag_events(df_lag)

    print()


def main():
    parser = argparse.ArgumentParser(description="ArtifactFlow observability report")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours")
    parser.add_argument(
        "--obs-dir",
        type=str,
        default="data/observability",
        help="Path to observability jsonl dir",
    )
    args = parser.parse_args()

    global pd
    pd = _require_pandas()

    asyncio.run(_run_report(args.hours, args.obs_dir))


if __name__ == "__main__":
    main()
