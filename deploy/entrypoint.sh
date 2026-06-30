#!/bin/sh
set -e

# Release vs serve (乙2 — single-run deploy gate).
#   - `entrypoint.sh release` → migrate + reconcile once, then EXIT (no serve). Run as a
#     one-shot compose `release` service that backends gate on via
#     `depends_on: { release: { condition: service_completed_successfully } }`, or as an
#     Ansible release task on multi-host. Backends then start with AF_SKIP_RELEASE=1.
#   - default (serve; "$@" = run_server) → if AF_SKIP_RELEASE is set, a dedicated release
#     step already migrated+reconciled → just serve. Otherwise (single-box / backward
#     compat) self-release inline before serving.
#
# Why a dedicated release step at scale: the inline path makes EVERY backend run reconcile
# (leader migrates under an advisory lock, followers re-reconcile) — correct (esp. after
# the #3 keep-on-env-absent fix) but redundant, and each replica reads its OWN env. One
# release step with one authoritative env is cleaner and makes the multi-host story
# trivial. The inline path is kept verbatim for Mode 1 (SQLite single box) + backward compat.
#
# NOTE: run_release()'s body is intentionally NOT indented — the PG branch embeds a
# python -c "..." whose Python lines must stay at column 0; re-indenting would corrupt it.
# That Python program lives inside a shell DOUBLE-quoted string, so backticks and $(...) and
# bare $ stay shell-active in it — even inside Python comments/strings. A stray `word` in a
# comment makes the shell run `word` as command substitution (harmless empty result, but it
# emits "word: not found" noise on every release). Keep the embedded program free of them.

DB_URL="${ARTIFACTFLOW_DATABASE_URLS:-$ARTIFACTFLOW_DATABASE_URL}"

run_release() {
case "$DB_URL" in
  *sqlite*|"")
    echo "SQLite or no DB configured — skipping Alembic migration"
    # SQLite 单副本:create_all 在 reconcile 脚本的 db.initialize() 里建表,
    # 然后把 config 物化进 DB(撞名/坏 config → 非零退出,set -e 阻断启动)。
    echo "Reconciling config -> DB..."
    python scripts/reconcile_config.py
    ;;
  *)
    # Use PG advisory lock to ensure only one replica runs migrations.
    # - Leader: acquires lock, runs alembic upgrade head.
    #   On failure: exits without releasing lock (conn close auto-releases).
    #   On success: releases lock, continues.
    # - Follower: waits for lock, then verifies schema is at head.
    #   If not at head (leader failed): exits non-zero.
    python -c "
import asyncio, os, subprocess, sys

def verify_at_head():
    \"\"\"Check that alembic current == head. Exit non-zero if not.\"\"\"
    result = subprocess.run(
        ['alembic', 'heads', '--resolve-dependencies'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f'Cannot determine head revision: {result.stderr}', file=sys.stderr)
        sys.exit(1)
    head = result.stdout.strip().split()[0] if result.stdout.strip() else ''

    result = subprocess.run(
        ['alembic', 'current'],
        capture_output=True, text=True,
    )
    current = result.stdout.strip().split()[0] if result.stdout.strip() else ''

    if current != head:
        print(f'Schema not at head (current={current!r}, head={head!r}). Migration may have failed.', file=sys.stderr)
        sys.exit(1)
    print(f'Schema verified at head: {head}')

async def migrate():
    url = (os.environ.get('ARTIFACTFLOW_DATABASE_URLS') or os.environ.get('ARTIFACTFLOW_DATABASE_URL', ''))
    # asyncpg needs raw postgresql:// URL (no +asyncpg suffix)
    dsn = url.split(',')[0].strip().replace('postgresql+asyncpg://', 'postgresql://')

    import asyncpg
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        acquired = await conn.fetchval(\"SELECT pg_try_advisory_lock(hashtext('alembic_migrate'))\")
        if acquired:
            print('Acquired migration lock, running Alembic...')
            result = subprocess.run(['alembic', 'upgrade', 'head'])
            if result.returncode != 0:
                # Do NOT release the lock — conn.close() will auto-release.
                # This ensures waiting replicas don't race ahead.
                print('Alembic migration failed', file=sys.stderr)
                sys.exit(result.returncode)
            # config -> DB reconcile under the advisory lock. In the inline path, followers
            # re-run it idempotently too (below) so every replica self-certifies config; the
            # dedicated release step runs only this leader path once (no followers).
            print('Reconciling config -> DB (leader)...')
            rec = subprocess.run(['python', 'scripts/reconcile_config.py'])
            if rec.returncode != 0:
                print('Config reconcile failed', file=sys.stderr)
                sys.exit(rec.returncode)
            await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
            print('Alembic migrations + config reconcile complete')
        else:
            print('Another replica is running migrations, waiting...')
            await conn.execute(\"SELECT pg_advisory_lock(hashtext('alembic_migrate'))\")
            try:
                print('Migration lock acquired, verifying schema...')
                verify_at_head()
                # Re-run reconcile idempotently: all-skip when config is good,
                # loud-fail when bad. Held under the lock => no concurrent run.
                print('Reconciling config -> DB (follower, idempotent)...')
                rec = subprocess.run(['python', 'scripts/reconcile_config.py'])
                if rec.returncode != 0:
                    print('Config reconcile failed', file=sys.stderr)
                    sys.exit(rec.returncode)
            finally:
                await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
    finally:
        await conn.close()

try:
    asyncio.run(migrate())
except SystemExit:
    raise
except Exception as e:
    # If advisory lock fails (e.g. MySQL), fall back to direct migration
    print(f'Advisory lock unavailable ({e}), running Alembic directly...')
    subprocess.run(['alembic', 'upgrade', 'head'], check=True)
    subprocess.run(['python', 'scripts/reconcile_config.py'], check=True)
"
    ;;
esac
}

# --- release mode: one-shot migrate + reconcile, no serve ---
if [ "$1" = "release" ]; then
    run_release
    echo "Release complete."
    exit 0
fi

# --- serve mode ---
if [ -n "$AF_SKIP_RELEASE" ]; then
    # A dedicated release step (compose `release` service / Ansible task) already migrated
    # + reconciled with one authoritative env. The compose `service_completed_successfully`
    # gate guarantees it succeeded before this backend starts; an empty/stale registry is
    # still caught at runtime (controller_factory's lead_agent guard).
    echo "AF_SKIP_RELEASE set — skipping inline migrate/reconcile (handled by release step)."
else
    run_release
fi

exec "$@"
