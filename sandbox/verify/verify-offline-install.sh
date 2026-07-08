#!/usr/bin/env bash
set -uo pipefail

# In-sandbox offline-install probe (plan §B / 原则 7 tier-2/3 delivery path).
# Installs the baked stub wheel with NO network via --no-index --find-links,
# proving the offline `pip install` that skill-asset / wheel-bundle dependencies
# rely on survives gVisor's Sentry. Run UNDER runsc --network=none.
#
# --target into a throwaway dir avoids needing write access to system
# site-packages as the non-root sandbox user.

FINDLINKS=/opt/stub-wheels
TARGET="$(mktemp -d)"
LOG="$(mktemp)"

if pip install --no-index --find-links "$FINDLINKS" --target "$TARGET" af-sandbox-stub >"$LOG" 2>&1 \
   && PYTHONPATH="$TARGET" python3 -c "import af_sandbox_stub; assert af_sandbox_stub.ping() == 'pong'"; then
  echo "  ✓ offline pip install --no-index --find-links + import (delivery path OK under runsc)"
  exit 0
fi

echo "  ✗ offline install/import FAILED:"
sed 's/^/      /' "$LOG"
exit 1
