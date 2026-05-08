#!/usr/bin/env bash
# Exit a maintenance window: bring backend / frontend up (optionally with
# a new version), wait for backend health, then lower the maintenance flag.
# If backend doesn't become healthy within 60s, the maintenance page stays
# on so the operator can investigate without exposing a half-broken service.
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
VERSION="${1:-${AF_VERSION:-latest}}"

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

echo -n "→ Waiting for backend healthy"
HEALTHY=0
for _ in $(seq 1 30); do
  cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q backend 2>/dev/null || true)
  if [[ -n "$cid" ]]; then
    state=$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
    if [[ "$state" == "healthy" ]]; then
      HEALTHY=1
      echo " ✓"
      break
    fi
  fi
  printf '.'
  sleep 2
done

if (( HEALTHY == 0 )); then
  echo
  echo "✗ backend 未在 60s 内 healthy，维护页保持开启"
  echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=80 backend"
  exit 1
fi

# /health is intentionally NOT gated by maintenance (see deploy/nginx.conf),
# so this probe goes through nginx and confirms upstream wiring is alive.
if ! curl -fs -m 5 "http://localhost/health/ready" >/dev/null; then
  echo "⚠ /health/ready 通过 nginx 不可达，维护页保持开启"
  echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 nginx backend"
  exit 1
fi

"$SCRIPT_DIR/maintenance.sh" off

echo
echo "✓ 服务已恢复，AF_VERSION=$VERSION"
