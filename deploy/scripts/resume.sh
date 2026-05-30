#!/usr/bin/env bash
# Exit a maintenance window for the INTRANET (Mode 3 / nginx) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/resume.sh [VERSION]
#
# VERSION is optional:
#   - provided → sets AF_VERSION, backend/frontend recreate with that image tag.
#   - omitted  → AF_VERSION env var, else compose default ("latest").
#
# Env knobs:
#   RESUME_HEALTHY_TIMEOUT  per-service healthy wait, seconds (default 60).
#                           Bump on slow-disk hosts (Next.js / FastAPI cold start
#                           > 60s).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.intranet.yml"
ENV_FILE="$ROOT/deploy/.env"
VERSION="${1:-${AF_VERSION:-latest}}"

MAINT_MODE_LABEL="Mode 3 / 内网"

# Through-proxy health probe: hit /health/ready via nginx's published host port.
# This exercises the FULL chain (host → nginx → backend), which is what catches
# the nginx static-upstream staleness gotcha after a backend recreate.
maint_probe() {
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
  # We deliberately do NOT `source` deploy/.env: Compose reads it as literal
  # KEY=VALUE text, but `bash` evaluates the file as shell — `$$` expands to the
  # script PID (mangling secrets like `PASSWORD=pa$$word`), `$abc` aborts under
  # `set -u`, and `$(cmd)` would execute.
  local http_port
  if [[ -n "${AF_HTTP_PORT+set}" ]]; then
    http_port="${AF_HTTP_PORT:-80}"
  else
    http_port=""
    if [[ -f "$ENV_FILE" ]]; then
      http_port=$(awk -F= '
        /^[[:space:]]*AF_HTTP_PORT[[:space:]]*=/ {
          val = $2
          sub(/^[[:space:]]*["'\'']?/, "", val)
          sub(/[^0-9].*$/, "", val)
          if (val != "") last = val
        }
        END { if (last != "") print last }
      ' "$ENV_FILE")
    fi
    http_port="${http_port:-80}"
  fi

  if ! curl -fs -m 5 "http://localhost:${http_port}/health/ready" >/dev/null; then
    echo "⚠ /health/ready 通过 nginx (port ${http_port}) 不可达，维护页保持开启"
    echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 nginx backend"
    return 1
  fi
}

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume "$VERSION"
