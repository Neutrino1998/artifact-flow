#!/bin/sh
set -e

DB_URL="${ARTIFACTFLOW_DATABASE_URLS:-$ARTIFACTFLOW_DATABASE_URL}"

case "$DB_URL" in
  *sqlite*|"")
    echo "SQLite or no DB configured — skipping Alembic migration"
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
            await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
            print('Alembic migrations complete')
        else:
            print('Another replica is running migrations, waiting...')
            await conn.execute(\"SELECT pg_advisory_lock(hashtext('alembic_migrate'))\")
            await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
            print('Migration lock released by peer, verifying schema...')
            verify_at_head()
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
"
    ;;
esac

exec "$@"
