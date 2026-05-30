#!/usr/bin/env bash
# Exit a maintenance window for the PUBLIC (Mode 2 / Caddy) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/resume-prod.sh
#
# No VERSION argument (unlike the intranet resume.sh): public images are built
# locally and pinned to :latest in docker-compose.prod.yml — there is no
# versioned tag to switch to. To upgrade public, change code then rebuild:
#   git pull --ff-only   # or git checkout <ref>
#   ./deploy/scripts/deploy-prod.sh --build
# resume-prod.sh just brings the CURRENT images back up after a maintenance
# window (e.g. an .env edit), it does not change versions.
#
# Env knobs:
#   RESUME_HEALTHY_TIMEOUT  per-service healthy wait, seconds (default 60).
#
# Probe note: Caddy owns fixed ports 80/443; probing localhost:443 from the host
# would fail TLS hostname verification (cert is for AF_DOMAIN, not localhost).
# So the probe runs INSIDE the caddy container against Caddy's OWN internal
# health listener (Caddyfile `:2021`, not published to the host) — this exercises
# the real Caddy → backend reverse_proxy path (config loaded + routing works),
# not just backend's liveness on caddy's network.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"

MAINT_MODE_LABEL="Mode 2 / 公网"

# Through-proxy health probe: exec into the caddy container and hit Caddy's
# internal health listener (`:2021` in the Caddyfile — HTTP, no TLS, NOT
# published to the host). The request flows client → Caddy(:2021) →
# reverse_proxy → backend:8000, so a green result means Caddy is up, its config
# loaded, and the proxy path to backend works. /health is not gated by
# maintenance, so this is safe to run before lifting the flag. Running
# in-container also avoids depending on the host firewall exposing 80/443 and
# sidesteps the TLS-on-localhost hostname mismatch.
maint_probe() {
  local caddy_cid
  caddy_cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null || true)
  if [[ -z "$caddy_cid" ]]; then
    echo "⚠ caddy 容器未运行，无法验证链路，维护页保持开启"
    echo "  先确保 caddy 已启动：${DC[*]} -f $COMPOSE_FILE --profile infra up -d caddy"
    return 1
  fi
  # caddy:alpine ships busybox wget (no curl). -q --spider = HEAD-ish probe.
  if ! docker exec "$caddy_cid" wget -q --spider -T 5 http://localhost:2021/health/ready; then
    echo "⚠ /health/ready 经 Caddy(:2021)→backend 不可达，维护页保持开启"
    echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 caddy backend"
    return 1
  fi
}

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume
