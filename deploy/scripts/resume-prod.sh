#!/usr/bin/env bash
# Exit a maintenance window for the PUBLIC (Mode 2 / Caddy) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/resume-prod.sh            # no arguments — rejects extras
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
# Probe: the shared default in _maint_lib.sh (exec into caddy, hit its :2021
# internal health listener → backend) — see the rationale there.

set -euo pipefail

# Public resume takes NO arguments — images are locally-built :latest, there is
# no version tag to switch to. Reject extras loudly so a stray
# `resume-prod.sh v2.3.0` (intranet muscle memory) fails instead of silently
# ignoring the version and lifting maintenance on the unchanged :latest image.
if (( $# > 0 )); then
  echo "resume-prod.sh takes no arguments (got: $*)." >&2
  echo "Public images are :latest — to change version, rebuild:" >&2
  echo "  git pull --ff-only && ./deploy/scripts/deploy-prod.sh --build" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"

MAINT_MODE_LABEL="Mode 2 / 公网"

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_resume
