#!/usr/bin/env bash
# preflight.sh — verify the two things this bundle delivers, nothing else.
#
#   1. analyst-tools/ wheels resolve offline (pandas+numpy)  — IF present locally
#   2. py-spy lives in the backend image                     — IF backend is running
#
# Anything else (Yama ptrace policy, host gdb/strace availability) is host
# concern and lives in docs/_archive/ops/cloud-service-checklist.md, not here.
#
# Usage:
#   deploy/scripts/preflight.sh                              # default analyst-tools path
#   deploy/scripts/preflight.sh /opt/af/analyst-tools        # explicit path
#   AF_COMPOSE_FILE=docker-compose.prod.yml ./preflight.sh   # non-intranet compose
#
# Exit: 0 = no failures (skips are OK); 1 = at least one ✗.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

ANALYST_DIR="${1:-$ROOT/analyst-tools}"
COMPOSE="${AF_COMPOSE_FILE:-$ROOT/deploy/docker-compose.intranet.yml}"

fail=0
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; fail=$((fail + 1)); }
skip() { printf '  ℹ %s\n' "$1"; }

# ---------------------------------------------------------------------------
# Check 1: analyst-tools wheels resolve offline
# ---------------------------------------------------------------------------
echo "→ analyst-tools wheels offline-resolve ($ANALYST_DIR)"
if [[ ! -d "$ANALYST_DIR" ]]; then
  skip "not present — analyst machine may be elsewhere"
elif [[ ! -d "$ANALYST_DIR/wheels" ]]; then
  bad "wheels/ subdir missing inside $ANALYST_DIR"
elif ! pip_bin=$(command -v pip || command -v pip3) || [[ -z "$pip_bin" ]]; then
  skip "pip not on PATH — re-run on the analyst host"
else
  # --ignore-installed: force pip to plan from --find-links instead of
  # short-circuiting on system-installed pandas (dev/build host case).
  if output=$("$pip_bin" install --no-index --find-links "$ANALYST_DIR/wheels" \
                --ignore-installed --dry-run pandas 2>&1); then
    ok "pandas resolves from $ANALYST_DIR/wheels"
  else
    bad "pandas does not resolve — wheel/Python ABI mismatch?"
    printf '%s\n' "$output" | head -3 | sed 's/^/      /'
  fi
fi

# ---------------------------------------------------------------------------
# Check 2: py-spy in backend container
# ---------------------------------------------------------------------------
echo
echo "→ py-spy in backend container (compose: $(basename "$COMPOSE"))"
if ! command -v docker >/dev/null 2>&1; then
  skip "docker not on PATH — re-run on the deployed host"
elif [[ ! -f "$COMPOSE" ]]; then
  skip "compose file not found at $COMPOSE — set AF_COMPOSE_FILE"
else
  backend=$(docker compose -f "$COMPOSE" ps -q backend 2>/dev/null | head -1 || true)
  if [[ -z "$backend" ]]; then
    skip "backend service not running — re-run after \`docker compose -f $COMPOSE up -d backend\`"
  elif version=$(docker exec "$backend" py-spy --version 2>&1); then
    ok "$version"
  else
    bad "py-spy missing from backend image — rebuild image"
    printf '      %s\n' "$version"
  fi
fi

# ---------------------------------------------------------------------------
echo
if (( fail > 0 )); then
  echo "✗ Preflight: $fail failure(s) — address before deploying"
  exit 1
fi
echo "✓ Preflight: OK (skips are fine — they mean the precondition isn't here yet)"
exit 0
