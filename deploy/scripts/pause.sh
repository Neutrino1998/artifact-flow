#!/usr/bin/env bash
# Enter a maintenance window for the INTRANET (Mode 3 / nginx) deployment.
# Thin entrypoint — shared logic lives in _maint_lib.sh.
#
# Usage:
#   deploy/scripts/pause.sh ["运维说明文案"]
#
# Prerequisite: nginx must already be running with the maintenance volume
# mounted (i.e. created from the new compose file). On a host upgrading
# for the first time, run once before pause.sh:
#   docker compose -f deploy/docker-compose.intranet.yml up -d --force-recreate nginx

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.intranet.yml"

MAINT_MODE_LABEL="Mode 3 / 内网"
MAINT_PROXY_LABEL="nginx"
MAINT_RESUME_HINT="resume.sh"

# shellcheck source=_maint_lib.sh
source "$SCRIPT_DIR/_maint_lib.sh"

maint_pause "${1:-}"

# Intranet air-gap upgrade reminders (beyond the shared core message).
echo "  prep (verify-bundle / docker load / tar xzf deploy + config) 应已在 pause 之前完成。"
echo "  若本版本动了 compose 的 nginx 块或 AF_HTTP_PORT，nginx 的 pre-pause force-recreate 也应已完成。"
echo "  完整流程见 docs/deployment.md 的「滚动更新已有部署」段。"
