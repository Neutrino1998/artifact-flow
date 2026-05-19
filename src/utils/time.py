"""时间工具:全链路 naive UTC 约定。

事故 2026-05-14 排查中 reviewer 在 Shanghai 部署测 observability_report
发现 8h 偏差:`datetime.now()` 写本地朴素,`server_default=func.now()`
在 SQLite/PG 上是 UTC naive,两者混用 → 时间窗查询错位。

约定:Python 侧任何会进入 DB 列、API 响应、序列化事件 payload 的
datetime,一律走 utc_now() 拿 naive UTC。DB 端 `server_default=func.now()`
仍是 schema 兜底,但 PG 部署需配 TIMEZONE=UTC 才能与 utc_now() 对齐
(SQLite/PG UTC 配置 / MySQL UTC 配置均自动对齐;非 UTC PG 偏量等于
session timezone 与 UTC 的差)。

仅以下场景保留 datetime.now()(本地)语义:
- 展示给 LLM 的"当前时间"提示词(context_manager.py:69)— LLM 看到
  user-local 时间才符合 UX 预期
- 纯 wall-clock duration 测量 — 理论上应改 time.monotonic(),但与本
  PR 时区话题无关,留给后续
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时间的 naive datetime(tzinfo=None)。

    naive 而非 aware 的原因:与项目当前 schema (`DateTime` 不带 timezone)
    对齐,SQLite 也无原生 tz 支持。aware UTC 留给未来跨 TZ 部署诉求出
    现时再做(direction 2,见 incident-2026-05-14-fix-plan PR-tz-unify)。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
