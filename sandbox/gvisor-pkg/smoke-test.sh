#!/usr/bin/env bash
set -uo pipefail   # NOT -e: run every tier, report all, exit nonzero if any fail

# Progressive gVisor smoke test (eval doc appendix B), Tier 0–5. Run as root on
# the target node AFTER install.sh + `systemctl reload docker`.
#
#   Tier 0  userns preflight (the BLOCKED check, eval §5.1) — STOP if it fails
#   Tier 1  runsc binary
#   Tier 2  Sentry platform (systrap always; kvm if /dev/kvm present)
#   Tier 3  docker runtime registration
#   Tier 4  container under runsc
#   Tier 5  in-container syscall probe
#
# Usage: sudo ./smoke-test.sh [IMAGE]
#   IMAGE defaults to artifactflow-sandbox:latest (load it first for Tier 4/5;
#   absent → Tier 4/5 reported as fail with a hint, lower tiers still run).

IMAGE="${1:-artifactflow-sandbox:latest}"
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
no(){ echo "  ✗ $1"; fail=$((fail+1)); }

echo "Tier 0 — userns preflight (BLOCKED check)"
if unshare -U /bin/true 2>/dev/null; then
  ok "unshare -U"
else
  no "unshare -U → this node is BLOCKED for gVisor"
  echo "     STOP: clone(CLONE_NEWUSER) is denied here. Do not deploy sandbox on this node."
  echo "     Hand the evidence pack (eval doc §5.3) to ops/vendor. Exiting."
  exit 1
fi

echo "Tier 1 — runsc binary"
runsc --version >/dev/null 2>&1 && ok "runsc --version" || no "runsc --version"

echo "Tier 2 — Sentry platform"
runsc --platform=systrap do echo ok >/dev/null 2>&1 && ok "systrap platform" || no "systrap platform"
if [[ -e /dev/kvm ]]; then
  runsc --platform=kvm do echo ok >/dev/null 2>&1 && ok "kvm platform" || no "kvm platform (will fall back to systrap)"
else
  echo "  · /dev/kvm absent — systrap only (expected on VMs without nested virt)"
fi

echo "Tier 3 — docker runtime registration"
docker info 2>/dev/null | grep -q runsc && ok "docker info lists runsc" || no "runsc absent from docker info (did you reload docker?)"

echo "Tier 4/5 — container + syscall probe under runsc (image: $IMAGE)"
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker run --rm --runtime=runsc --network=none "$IMAGE" \
    python3 -c "import os; os.listdir('/proc'); print(os.uname().sysname)" >/dev/null 2>&1 \
    && ok "container runs + /proc + uname under runsc" \
    || no "container/syscall probe under runsc"
else
  no "image $IMAGE not loaded — Tier 4/5 skipped (docker load it first)"
fi

echo
echo "smoke: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
