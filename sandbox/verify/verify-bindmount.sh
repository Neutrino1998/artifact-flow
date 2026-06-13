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

# 5. git over a bind-mounted repo whose .git owner != container uid 1000 — the
#    scenario the baked safe.directory='*' exists for (mounted trees carry a
#    host uid; git's dubious-ownership check would otherwise reject every op).
#    Repo is created by the image's OWN git as in-container root (-u 0), so the
#    probe needs no git on the host and the .git owner (0) != 1000 by
#    construction. Without the waiver this errors "detected dubious ownership".
#    macOS caveat: Docker Desktop's virtiofs presents bind-mounted files as
#    owned by the ACCESSING uid, so this check cannot fail on a mac rehearsal —
#    the discriminating run is on Linux (Kylin), where ownership is preserved.
if docker run --rm --runtime="$RUNTIME" --network=none -v "$HOSTDIR:/work" -u 0 "$IMAGE" \
     sh -c 'cd /work && git init -q rootrepo && cd rootrepo && echo x > f.txt && git add f.txt && git commit -qm seed' \
   && docker run --rm --runtime="$RUNTIME" --network=none -v "$HOSTDIR:/work" "$IMAGE" \
     sh -c 'cd /work/rootrepo && git status --short >/dev/null && git log --oneline | grep -q seed'; then
  ok "git on root-owned bind-mounted repo (safe.directory waiver)"
else
  no "git on root-owned bind-mounted repo (dubious-ownership? check baked safe.directory='*')"
fi

# probe dir now contains root-owned files (check 5) — plain rm -rf may fail for
# a non-root host user; fall back to deleting from a root container.
rm -rf "$HOSTDIR" 2>/dev/null || docker run --rm --network=none -v "$HOSTDIR:/work" -u 0 "$IMAGE" \
  sh -c 'rm -rf /work/rootrepo' && rm -rf "$HOSTDIR"
echo
echo "bindmount: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
