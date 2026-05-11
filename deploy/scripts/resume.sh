#!/usr/bin/env bash
# Exit a maintenance window: bring backend / frontend up (optionally with
# a new version), wait for both to become healthy, then lower the maintenance
# flag. If either container fails to become healthy within 60s, or the nginx
# probe fails, the maintenance page stays on so the operator can investigate
# without exposing a half-broken service.
#
# Usage:
#   deploy/scripts/resume.sh [VERSION]
#
# VERSION is optional:
#   - If provided, sets AF_VERSION → backend/frontend recreate with that image tag.
#   - If omitted, falls back to the AF_VERSION env var, otherwise compose default ("latest").

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.intranet.yml"
ENV_FILE="$ROOT/deploy/.env"
VERSION="${1:-${AF_VERSION:-latest}}"

# Resolve AF_HTTP_PORT exactly the way Compose substitutes
# `${AF_HTTP_PORT:-80}` in the compose file:
#   1. Shell env explicitly set (even to "") → that value, with `:-` empty
#      fallback to 80. Crucially, `AF_HTTP_PORT= ./resume.sh` must yield
#      80 (not the .env value), because Compose itself treats shell-empty
#      as overriding .env and then `:-` defaults to 80.
#   2. Shell env unset → consult deploy/.env.
#   3. Otherwise → 80.
# `${VAR+x}` (no colon) distinguishes set-but-empty from unset; `${VAR:-80}`
# (with colon) handles the empty-fallback for case (1).
#
# We deliberately do NOT `source` deploy/.env: Compose reads it as
# literal KEY=VALUE text, but `bash` evaluates the file as shell —
# `$$` expands to the script PID (mangling secrets like
# `PASSWORD=pa$$word`), `$abc` aborts under `set -u`, and `$(cmd)` would
# execute. Compose itself loads .env from the compose-file directory,
# so we don't need to export anything to the shell.
if [[ -n "${AF_HTTP_PORT+set}" ]]; then
  HTTP_PORT="${AF_HTTP_PORT:-80}"
else
  HTTP_PORT=""
  if [[ -f "$ENV_FILE" ]]; then
    HTTP_PORT=$(awk -F= '
      /^[[:space:]]*AF_HTTP_PORT[[:space:]]*=/ {
        val = $2
        sub(/^[[:space:]]*["'\'']?/, "", val)
        sub(/[^0-9].*$/, "", val)
        if (val != "") last = val
      }
      END { if (last != "") print last }
    ' "$ENV_FILE")
  fi
  HTTP_PORT="${HTTP_PORT:-80}"
fi

# Pick docker compose CLI: V2 plugin ("docker compose") on dev hosts, V1
# standalone ("docker-compose") on older intranet hosts. Both speak the
# same compose-file syntax we use.
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Error: neither 'docker compose' nor 'docker-compose' available" >&2
  exit 1
fi

echo "→ Starting backend / frontend (AF_VERSION=$VERSION)"
AF_VERSION="$VERSION" "${DC[@]}" -f "$COMPOSE_FILE" up -d backend frontend

wait_healthy() {
  local svc="$1" label="$2"
  echo -n "→ Waiting for $label healthy"
  for _ in $(seq 1 30); do
    local cid state
    cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)
    if [[ -n "$cid" ]]; then
      state=$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
      if [[ "$state" == "healthy" ]]; then
        echo " ✓"
        return 0
      fi
    fi
    printf '.'
    sleep 2
  done
  echo
  echo "✗ $label 未在 60s 内 healthy，维护页保持开启"
  echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=80 $svc"
  return 1
}

# Backend AND frontend must both be healthy before lifting maintenance —
# nginx routes `/` to frontend, so a crash-looping frontend would 502 users
# the instant maintenance flips off if we only gated on backend.
wait_healthy backend  backend  || exit 1
wait_healthy frontend frontend || exit 1

# /health is intentionally NOT gated by maintenance (see deploy/nginx.conf),
# so this probe goes through nginx and confirms upstream wiring is alive.
# Use AF_HTTP_PORT from .env — pinning :80 breaks any host-port override.
if ! curl -fs -m 5 "http://localhost:${HTTP_PORT}/health/ready" >/dev/null; then
  echo "⚠ /health/ready 通过 nginx (port ${HTTP_PORT}) 不可达，维护页保持开启"
  echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 nginx backend"
  exit 1
fi

"$SCRIPT_DIR/maintenance.sh" off

echo
echo "✓ 服务已恢复，AF_VERSION=$VERSION"
