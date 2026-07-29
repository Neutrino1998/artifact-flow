#!/usr/bin/env python3
"""Stage-0 CLI for the fully stopped native history migration.

Only ``scan`` and ``report`` exist in stage 0.  They never write the source
database.  The backend must already be fully stopped; this CLI intentionally
does not grow an online-writer detector or head-fingerprint reconciliation
path.  Stage 5 adds generate/apply/verify on top of the same checkpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from native_tool_history.checkpoint import Checkpoint, CheckpointError
from native_tool_history.manifest import ManifestError, scan_database


def resolve_database_url() -> str:
    urls = os.getenv("ARTIFACTFLOW_DATABASE_URLS", "")
    if urls:
        first = urls.split(",", 1)[0].strip()
        if first:
            return first
    url = os.getenv("ARTIFACTFLOW_DATABASE_URL", "")
    if not url:
        raise RuntimeError("ARTIFACTFLOW_DATABASE_URL(S) is not configured")
    return url


def _assert_separate_checkpoint(checkpoint: Path, database_url: str) -> None:
    url = make_url(database_url)
    if not url.get_backend_name().startswith("sqlite") or not url.database:
        return
    source = Path(url.database)
    if not source.is_absolute():
        source = ROOT / source
    if checkpoint.resolve() == source.resolve():
        raise RuntimeError("checkpoint must not be the source application database")


def _print_report(report: dict) -> None:
    source = report["source"]
    leaves = report["leaves"]
    print(f"migration_id={report['migration_id']} status={report['status']}")
    print(
        "source: "
        f"conversations={source['conversations']} messages={source['messages']} "
        f"empty_conversations={source['empty_conversations']}"
    )
    print(
        "leaves: "
        f"total={leaves['total']} active={leaves['active']} "
        f"max_path_messages={leaves['max_path_messages']}"
    )
    for kind, counts in report["tasks"].items():
        print(f"tasks.{kind}: {counts}")
    print(f"observations: {report['observations']}")
    print(f"blocking: {report['blocking']}")
    print(f"ready_for_apply={str(report['ready_for_apply']).lower()}")


async def _scan(args: argparse.Namespace) -> int:
    database_url = resolve_database_url()
    _assert_separate_checkpoint(args.checkpoint, database_url)
    checkpoint = Checkpoint(args.checkpoint.resolve())
    if checkpoint.run_exists(args.migration_id):
        if not args.resume:
            raise CheckpointError(
                f"migration {args.migration_id!r} already exists; pass --resume"
            )
        _print_report(checkpoint.report(args.migration_id))
        return 0

    if args.resume:
        raise CheckpointError(
            f"migration {args.migration_id!r} does not exist; remove --resume"
        )
    scan = await scan_database(database_url)
    checkpoint.create_scan(
        args.migration_id,
        make_url(database_url).get_backend_name(),
        scan,
    )
    _print_report(checkpoint.report(args.migration_id))
    return 0


def _report(args: argparse.Namespace) -> int:
    database_url = resolve_database_url()
    _assert_separate_checkpoint(args.checkpoint, database_url)
    report = Checkpoint(args.checkpoint.resolve()).report(args.migration_id)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["ready_for_apply"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan the fully stopped source DB once.")
    scan.add_argument("--checkpoint", type=Path, required=True)
    scan.add_argument("--migration-id", required=True)
    scan.add_argument(
        "--resume",
        action="store_true",
        help="Reuse an existing immutable scan; do not query the source DB again.",
    )

    report = subparsers.add_parser("report", help="Read one checkpoint report.")
    report.add_argument("--checkpoint", type=Path, required=True)
    report.add_argument("--migration-id", required=True)
    report.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "scan":
            code = asyncio.run(_scan(args))
        else:
            code = _report(args)
    except (CheckpointError, ManifestError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
