#!/usr/bin/env bash
# Exit a maintenance window for the INTRANET (Mode 3 / Caddy) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh, including the
# through-proxy health probe (exec into caddy, hit its :2021 internal health
# listener → backend). No host-port arithmetic: the probe doesn't depend on
# AF_HTTP_PORT/AF_HTTPS_PORT publishing, and Caddy re-resolves upstreams at
# request time so there is no stale-upstream gotcha to exercise.
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
VERSION="${1:-${AF_VERSION:-latest}}"

MAINT_MODE_LABEL="Mode 3 / 内网"

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume "$VERSION"
