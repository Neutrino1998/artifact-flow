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
ACTIVE_ROOT="$(readlink -f "$ROOT/.artifactflow/current" 2>/dev/null || true)"
if [[ -n "$ACTIVE_ROOT" && -f "$ACTIVE_ROOT/deploy/docker-compose.intranet.yml" ]]; then
  COMPOSE_FILE="$ACTIVE_ROOT/deploy/docker-compose.intranet.yml"
else
  COMPOSE_FILE="$ROOT/deploy/docker-compose.intranet.yml"
fi
MAINT_ENV_FILE="$ROOT/deploy/.env"
MAINT_RUNTIME_DEPLOY_DIR="$ROOT/deploy"
if [[ -f "$MAINT_ENV_FILE" \
   && "$(awk -F= '$1 == "AF_ENABLE_SANDBOX" {print $2; exit}' "$MAINT_ENV_FILE")" == 1 \
   && -f "${ACTIVE_ROOT:-$ROOT}/deploy/docker-compose.sandbox.yml" ]]; then
  MAINT_EXTRA_COMPOSE_FILE="${ACTIVE_ROOT:-$ROOT}/deploy/docker-compose.sandbox.yml"
fi
ACTIVE_APP_VERSION=""
if [[ -n "$ACTIVE_ROOT" && -f "$ACTIVE_ROOT/.af-release" ]]; then
  ACTIVE_APP_VERSION="$(awk -F= '$1=="app_version"{print $2; exit}' "$ACTIVE_ROOT/.af-release")"
fi
VERSION="${1:-${AF_VERSION:-${ACTIVE_APP_VERSION:-latest}}}"

MAINT_MODE_LABEL="Mode 3 / 内网"

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume "$VERSION"
