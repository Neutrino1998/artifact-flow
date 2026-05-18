"""
observability_report.py — 跑一下就能看的 obs 报告

数据源:
  1. MessageEvent JSON 列 (业务侧:LLM / 工具调用)
  2. data/observability/metrics.jsonl* (运行时:loop_lag / RSS / DB pool / Redis)
  3. data/observability/loop-lag.jsonl* (事件:loop 软退化触发记录)

用法:
    python scripts/observability_report.py             # 24h 窗口
    python scripts/observability_report.py --hours 72  # 72h 窗口

pandas 是分析工具,不在 runtime requirements.txt;`pip install pandas` 或走
release bundle 的离线 wheel 安装。硬 wedge dump 看 docker logs backend
找 faulthandler 的 "Thread 0x..." 行,本脚本不聚合。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from glob import glob
from pathlib import Path

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


from sqlalchemy import create_engine


def _resolve_engine_url() -> str:
    """对齐 config.effective_database_url 的优先级:DATABASE_URLS 优先,DATABASE_URL 兜底。

    生产同时设了二者时,主应用写的是 URLS 的第一个;脚本反过来读会查错库。
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
        url = "sqlite:///data/artifactflow.db"
    # pd.read_sql 走 sync driver — 把 aiosqlite/asyncpg/aiomysql 换成同步版本
    url = url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2").replace(
        "+aiomysql", "+pymysql"
    )
    return url


def _load_message_events(engine, hours: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (df_llm, df_tool)。"""
    # 对应 PostgreSQL JSON 运算;SQLite 走 json_extract,语法不同
    dialect = engine.dialect.name

    if dialect == "postgresql":
        llm_sql = f"""
            SELECT created_at, agent_name,
                   data->>'model' AS model,
                   (data->'token_usage'->>'input_tokens')::int  AS in_tok,
                   (data->'token_usage'->>'output_tokens')::int AS out_tok,
                   (data->>'duration_ms')::int AS dur_ms
            FROM message_events
            WHERE event_type='llm_complete'
              AND created_at > now() - interval '{hours} hours'
        """
        tool_sql = f"""
            SELECT created_at,
                   data->>'tool' AS tool,
                   (data->>'duration_ms')::int AS dur_ms,
                   (data->>'success')::bool   AS success,
                   data->'metadata'           AS metadata
            FROM message_events
            WHERE event_type='tool_complete'
              AND created_at > now() - interval '{hours} hours'
        """
    elif dialect == "sqlite":
        llm_sql = f"""
            SELECT created_at, agent_name,
                   json_extract(data, '$.model') AS model,
                   CAST(json_extract(data, '$.token_usage.input_tokens')  AS INTEGER) AS in_tok,
                   CAST(json_extract(data, '$.token_usage.output_tokens') AS INTEGER) AS out_tok,
                   CAST(json_extract(data, '$.duration_ms')               AS INTEGER) AS dur_ms
            FROM message_events
            WHERE event_type='llm_complete'
              AND datetime(created_at) > datetime('now', '-{hours} hours')
        """
        tool_sql = f"""
            SELECT created_at,
                   json_extract(data, '$.tool') AS tool,
                   CAST(json_extract(data, '$.duration_ms') AS INTEGER) AS dur_ms,
                   json_extract(data, '$.success') AS success,
                   json_extract(data, '$.metadata') AS metadata
            FROM message_events
            WHERE event_type='tool_complete'
              AND datetime(created_at) > datetime('now', '-{hours} hours')
        """
    else:
        # MySQL/MariaDB 用 JSON_EXTRACT;留口子,实际生产是 PG
        raise NotImplementedError(f"Dialect {dialect} not implemented")

    df_llm = pd.read_sql(llm_sql, engine)
    df_tool = pd.read_sql(tool_sql, engine)

    # SQLite 的 metadata 拿回来是 JSON 文本,要二次解析才能给 json_normalize
    if dialect == "sqlite":
        df_tool["metadata"] = df_tool["metadata"].apply(
            lambda v: json.loads(v) if v else None
        )
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


def _print_llm_summary(df_llm: pd.DataFrame) -> None:
    print("\n=== LLM calls (24h, by model × agent) ===")
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


def _print_tool_summary(df_tool: pd.DataFrame) -> None:
    print("\n=== Tool calls (24h, by tool) ===")
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

    print(f"ArtifactFlow observability report (last {args.hours}h)")
    print("=" * 70)

    # MessageEvent
    try:
        engine = create_engine(_resolve_engine_url())
        df_llm, df_tool = _load_message_events(engine, args.hours)
        _print_llm_summary(df_llm)
        _print_tool_summary(df_tool)
        _print_fuzzy_stats(df_tool)
    except Exception as e:
        print(f"\n[skip] MessageEvent: {e}", file=sys.stderr)

    # Runtime metrics
    metrics_glob = str(Path(args.obs_dir) / "metrics.jsonl*")
    df_runtime = _load_jsonl_glob(metrics_glob)
    _print_runtime_summary(df_runtime)

    # Loop-lag events
    lag_glob = str(Path(args.obs_dir) / "loop-lag.jsonl*")
    df_lag = _load_jsonl_glob(lag_glob)
    _print_lag_events(df_lag)

    print()


if __name__ == "__main__":
    main()
