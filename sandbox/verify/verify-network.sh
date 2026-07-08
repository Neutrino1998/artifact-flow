#!/usr/bin/env bash
set -uo pipefail

# HOST-side network policy probe (plan §B / 原则 7). Verifies the two ends of the
# spectrum that are mechanically testable on an air-gapped node, and documents
# the allowlist middle ground (which is a host firewall rule, not a docker flag).
#
#   1) --network=none  → sandbox is fully isolated (no egress, even internal)
#   2) default bridge  → egress works + runsc netstack resolves DNS
#
# Because the intranet has no public internet, "public blocked" can't be told
# apart from "no internet at all" — so we test reachability of an INTERNAL host
# you name, under none (must be BLOCKED) vs bridge (must be OPEN). The default
# PRODUCTION policy is --network=none (原则 7); allowlist is the retained fallback.
#
# Env:
#   IMAGE       default artifactflow-sandbox:latest
#   RUNTIME     default runsc
#   PROBE_HOST  internal ip:port reachable from the docker host (e.g. the
#               gateway, or the PG host). Reachability deltas skipped if unset.
#   PROBE_NAME  internal hostname to resolve (exercises runsc netstack DNS).

IMAGE="${IMAGE:-artifactflow-sandbox:latest}"
RUNTIME="${RUNTIME:-runsc}"
PROBE_HOST="${PROBE_HOST:-}"
PROBE_NAME="${PROBE_NAME:-}"
pass=0; fail=0; skip=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
no(){ echo "  ✗ $1"; fail=$((fail+1)); }
sk(){ echo "  ⊘ $1"; skip=$((skip+1)); }

# TCP connect attempt from inside the sandbox to host:port under a given network.
# Prints OPEN / BLOCKED:<reason>.
conn(){ # net host port
  docker run --rm -i --runtime="$RUNTIME" --network="$1" "$IMAGE" python3 - "$2" "$3" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket()
s.settimeout(3)
try:
    s.connect((host, port))
    print("OPEN")
    s.close()
except Exception as e:
    print(f"BLOCKED: {e}")
PY
}

echo "1) --network=none → must be isolated"
if [[ -n "$PROBE_HOST" ]]; then
  h="${PROBE_HOST%:*}"; p="${PROBE_HOST##*:}"
  out="$(conn none "$h" "$p" 2>/dev/null)"
  [[ "$out" == BLOCKED* ]] && ok "none: $PROBE_HOST unreachable ($out)" || no "none: expected BLOCKED, got '$out'"
else
  sk "none reachability — set PROBE_HOST=ip:port to test"
fi

echo "2) default bridge → egress works + runsc netstack DNS"
if [[ -n "$PROBE_HOST" ]]; then
  h="${PROBE_HOST%:*}"; p="${PROBE_HOST##*:}"
  out="$(conn bridge "$h" "$p" 2>/dev/null)"
  [[ "$out" == OPEN ]] && ok "bridge: $PROBE_HOST reachable" || no "bridge: expected OPEN, got '$out'"
else
  sk "bridge reachability — set PROBE_HOST=ip:port"
fi
if [[ -n "$PROBE_NAME" ]]; then
  out="$(docker run --rm -i --runtime="$RUNTIME" --network=bridge "$IMAGE" \
         python3 -c "import socket,sys; print(socket.gethostbyname(sys.argv[1]))" "$PROBE_NAME" 2>&1)"
  [[ "$out" =~ ^[0-9]+\.[0-9] ]] && ok "DNS under runsc: $PROBE_NAME → $out" || no "DNS under runsc: '$out'"
else
  sk "DNS — set PROBE_NAME=internal.hostname"
fi

cat <<'NOTE'

  NOTE: the allowlist middle ground (egress to ONLY the deps mirror) is a HOST
  firewall rule scoping bridge egress to mirror-ip:443 — not a docker flag.
  Test it after pointing a rule at the operator's chosen mirror. Default
  production policy is --network=none (原则 7); allowlist is the kept fallback.
NOTE

echo "network: $pass passed, $fail failed, $skip skipped"
[[ $fail -eq 0 ]]
