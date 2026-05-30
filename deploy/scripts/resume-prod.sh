#!/usr/bin/env bash
# Exit a maintenance window for the PUBLIC (Mode 2 / Caddy) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/resume-prod.sh [VERSION]
#
# VERSION is optional:
#   - provided → sets AF_VERSION, backend/frontend recreate with that image tag.
#   - omitted  → AF_VERSION env var, else compose default ("latest").
#
# Env knobs:
#   RESUME_HEALTHY_TIMEOUT  per-service healthy wait, seconds (default 60).
#
# Simpler probe than the intranet resume.sh: Caddy owns fixed ports 80/443 (no
# AF_HTTP_PORT host-port resolution), and probing localhost:443 from the host
# would fail TLS hostname verification (cert is for AF_DOMAIN, not localhost).
# So the probe runs INSIDE the caddy container against backend:8000 instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
VERSION="${1:-${AF_VERSION:-latest}}"

MAINT_MODE_LABEL="Mode 2 / 公网"

# Through-proxy health probe: exec into the caddy container and hit the backend
# health endpoint over Caddy's own network. /health is not gated by maintenance,
# so this confirms Caddy → backend is alive before lifting the flag. Run
# in-container so it doesn't depend on the host firewall exposing 80/443, and to
# sidestep TLS-on-localhost hostname mismatch.
maint_probe() {
  local caddy_cid
  caddy_cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null || true)
  if [[ -z "$caddy_cid" ]]; then
    echo "⚠ caddy 容器未运行，无法验证链路，维护页保持开启"
    echo "  先确保 caddy 已启动：${DC[*]} -f $COMPOSE_FILE --profile infra up -d caddy"
    return 1
  fi
  # caddy:alpine ships busybox wget (no curl). -q --spider = HEAD-ish probe.
  if ! docker exec "$caddy_cid" wget -q --spider -T 5 http://backend:8000/health/ready; then
    echo "⚠ /health/ready 经 Caddy→backend 不可达，维护页保持开启"
    echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 caddy backend"
    return 1
  fi
}

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume "$VERSION"
