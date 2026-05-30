#!/usr/bin/env bash
# Enter a maintenance window for the PUBLIC (Mode 2 / Caddy) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/pause-prod.sh ["运维说明文案"]
#
# The maintenance MECHANISM is identical to the intranet pause.sh; only the
# compose file and reverse proxy differ. Caddy stays running so the maintenance
# page stays reachable and the TLS cert keeps auto-renewing.
#
# Prerequisite: the caddy container must already be running from the new compose
# (it mounts deploy/maintenance at /etc/caddy/maintenance). First-time switch
# from an old nginx-based deployment:
#   docker compose -f docker-compose.prod.yml --profile infra up -d

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"

MAINT_MODE_LABEL="Mode 2 / 公网"
MAINT_PROXY_LABEL="Caddy"
MAINT_PROXY_EXTRA="，HTTPS 不中断"
MAINT_RESUME_HINT="resume-prod.sh"

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_pause "${1:-}"
