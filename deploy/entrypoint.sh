#!/bin/sh
set -e

DB_URL="${ARTIFACTFLOW_DATABASE_URLS:-$ARTIFACTFLOW_DATABASE_URL}"

case "$DB_URL" in
  *sqlite*|"")
    echo "SQLite or no DB configured — skipping Alembic migration"
    ;;
  *)
    # Use PG advisory lock to ensure only one replica runs migrations.
    # Other replicas wait for the lock holder to finish, then skip.
    python -c "
import asyncio, os, subprocess, sys

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
            await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
            if result.returncode != 0:
                sys.exit(result.returncode)
            print('Alembic migrations complete')
        else:
            print('Another replica is running migrations, waiting...')
            await conn.execute(\"SELECT pg_advisory_lock(hashtext('alembic_migrate'))\")
            await conn.execute(\"SELECT pg_advisory_unlock(hashtext('alembic_migrate'))\")
            print('Migration lock released by peer, continuing')
    finally:
        await conn.close()

try:
    asyncio.run(migrate())
except Exception as e:
    # If advisory lock fails (e.g. MySQL), fall back to direct migration
    print(f'Advisory lock unavailable ({e}), running Alembic directly...')
    subprocess.run(['alembic', 'upgrade', 'head'], check=True)
"
    ;;
esac

exec "$@"
