#!/usr/bin/env bash
set -uo pipefail

# HOST-side bind-mount + uid-mapping probe (plan §B). The container (non-root
# uid 1000) writes into a host-mounted dir; the host reads it back and reports
# the resulting owner uid. Also runs ripgrep over the mounted tree from inside
# the sandbox — the FS-traversal (getdents64/statx) syscall surface on the gofer
# filesystem, distinct from the in-container tmpfs path.
#
# Env: IMAGE (default artifactflow-sandbox:latest), RUNTIME (default runsc).

IMAGE="${IMAGE:-artifactflow-sandbox:latest}"
RUNTIME="${RUNTIME:-runsc}"
HOSTDIR="$(mktemp -d)"
# Container uid 1000 must be able to write here regardless of who runs this
# script on the host — wide-open perms are fine for a throwaway probe dir.
chmod 0777 "$HOSTDIR"
pass=0; fail=0
ok(){ echo "  ✓ $1"; pass=$((pass+1)); }
no(){ echo "  ✗ $1"; fail=$((fail+1)); }

# 1. container writes into the bind-mount
if docker run --rm --runtime="$RUNTIME" --network=none -v "$HOSTDIR:/work" "$IMAGE" \
     sh -c 'echo hello-from-sandbox > /work/out.txt && mkdir -p /work/sub && echo NEEDLE > /work/sub/find.txt'; then
  ok "container wrote to bind-mount"
else
  no "container write to bind-mount"
fi

# 2. host reads it back
if [[ -f "$HOSTDIR/out.txt" ]] && grep -q hello-from-sandbox "$HOSTDIR/out.txt"; then
  ok "host read back content"
else
  no "host read back content"
fi

# 3. uid mapping — report what the host sees (1000 unless docker userns-remap is on)
owner="$(stat -c '%u' "$HOSTDIR/out.txt" 2>/dev/null || echo '?')"
echo "      host-side owner uid of container-written file: ${owner} (expect 1000; note if remapped)"

# 4. ripgrep over the bind-mount from inside the sandbox (FS-traversal syscalls)
if docker run --rm --runtime="$RUNTIME" --network=none -v "$HOSTDIR:/work" "$IMAGE" \
     rg -l NEEDLE /work 2>/dev/null | grep -q find.txt; then
  ok "ripgrep over bind-mount (getdents/statx on gofer)"
else
  no "ripgrep over bind-mount"
fi

rm -rf "$HOSTDIR"
echo
echo "bindmount: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
