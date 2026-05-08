#!/usr/bin/env bash
# Enter a maintenance window: raise the maintenance page, then stop
# backend / frontend. Postgres, Redis, and Nginx are left running so the
# maintenance page stays reachable and DB state is preserved.
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
NOTE="${1:-}"

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

if [[ -n "$NOTE" ]]; then
  "$SCRIPT_DIR/maintenance.sh" on "$NOTE"
else
  "$SCRIPT_DIR/maintenance.sh" on
fi

# Brief settle so any in-flight requests get the maintenance page rather
# than a connection drop the instant the upstream goes away.
sleep 2

echo "→ Stopping backend / frontend"
"${DC[@]}" -f "$COMPOSE_FILE" stop backend frontend

echo
echo "✓ 维护窗口已开启"
echo "  • 用户访问 → 维护页（nginx 仍在运行）"
echo "  • backend / frontend 已停止"
echo "  • postgres / redis 仍在运行"
echo
echo "下一步：docker load 新镜像 / tar xzf 新 config / 编辑 .env，然后 resume.sh [VERSION]"
