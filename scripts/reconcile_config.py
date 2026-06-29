#!/usr/bin/env python3
"""
config → DB reconcile —— 把 config/tools + config/agents 物化进 DB 注册表。

Usage:
    python scripts/reconcile_config.py            # 跑 reconcile
    python scripts/reconcile_config.py --dry-run  # 只解析+报告,不写库

Dev:本地手动跑(改了 config/tools 或 config/agents 后)。
Prod:`deploy/entrypoint.sh` 在 migration 后、起 uvicorn 前于 leader 槽调用本脚本
(复用 PG advisory lock);**绝不在 per-worker FastAPI lifespan 跑**(每副本互写,原则 5)。

坏 config / 撞名 / unit 名违规 → 非零退出(启动期 loud-fail,同 create_admin 风格)。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 与 scripts/* 对齐:注入 src 路径 + load_dotenv
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from dotenv import load_dotenv

load_dotenv()


async def main(dry_run: bool) -> int:
    from config import config, validate_config
    from db.database import DatabaseManager
    from reconcile.reconciler import reconcile_config_to_db
    from utils.logger import get_logger

    logger = get_logger("ArtifactFlow")

    # 部署门禁(reviewer N4):release(非 dry-run)先全量校验 config —— 缺/格式错主密钥
    # (及其它必填项)在 release 闸即 loud-fail,而非"无凭证工具时 release 成功、backend
    # 启动才在 validate_config 崩 → crash-loop"。dry-run(纯解析报告)跳过,免逼着配齐 env。
    if not dry_run:
        validate_config()

    db_urls = [u.strip() for u in config.DATABASE_URLS.split(",") if u.strip()] if config.DATABASE_URLS else []
    db = DatabaseManager(
        database_url=config.effective_database_url,
        database_urls=db_urls if len(db_urls) > 1 else None,
    )
    await db.initialize()
    try:
        async with db.session() as session:
            report = await reconcile_config_to_db(session, commit=not dry_run)
        prefix = "DRY-RUN " if dry_run else ""
        print(f"{prefix}{report.summary()}")
        if report.created:
            print("  created:", ", ".join(report.created))
        if report.updated:
            print("  updated:", ", ".join(report.updated))
        if report.pruned:
            print("  pruned :", ", ".join(report.pruned))
        return 0
    except Exception as e:
        # 坏 config / 撞名 = loud-fail(同 JWT_SECRET 缺失即停)
        logger.exception("config→DB reconcile failed: %s", e)
        print(f"reconcile FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile config/ into the DB registry")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + report only, do not write to DB")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run)))
