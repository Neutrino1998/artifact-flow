#!/bin/sh
set -e

DB_URL="${ARTIFACTFLOW_DATABASE_URLS:-$ARTIFACTFLOW_DATABASE_URL}"

case "$DB_URL" in
  *sqlite*|"")
    echo "SQLite or no DB configured — skipping Alembic migration"
    ;;
  *)
    echo "Running Alembic migrations..."
    alembic upgrade head
    echo "Alembic migrations complete"
    ;;
esac

exec "$@"
