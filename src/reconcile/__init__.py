"""
config → DB reconciler(种子 ingest 横切底座,原则 5 / 决策 5)。

把 config 作者真相(`config/tools/`、`config/agents/`)物化进 DB 注册表
(`tool_units`/`tool_members`/`agents`/`agent_units`)。config 仍唯一作者真相,DB 只是
物化缓存:`seeded` 行 reconciler 拥有、UI 不可改;`dynamic` 行(UI 新建)reconciler 绝不碰。

设计:docs/_archive/design/skill-system-phase-b-design.md。
入口 = `scripts/reconcile_config.py`(独立脚本,同 create_admin 风格)。
  - dev:改完 config/tools|agents 后手动 `python scripts/reconcile_config.py`。
  - prod:`deploy/entrypoint.sh` 在 migration 后、起 uvicorn 前于 leader 槽调用该脚本
    (复用 PG advisory lock)。**绝不在 per-worker lifespan**(每副本互写,原则 5)。
"""

from reconcile.report import ReconcileReport
from reconcile.reconciler import reconcile_config_to_db

__all__ = ["ReconcileReport", "reconcile_config_to_db"]
