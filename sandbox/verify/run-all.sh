#!/usr/bin/env bash
set -uo pipefail

# One-shot §B verification orchestrator. Run on the Kylin node AFTER:
#   1. gVisor installed + `smoke-test.sh` passed   (sandbox/gvisor-pkg/)
#   2. sandbox image loaded                          (docker load < dist/artifactflow-sandbox-*.tar.gz)
#
# Runs all §B probes under runsc and prints a single PASS/FAIL summary.
# In-container probes are bind-mounted from this dir into /opt/verify (so they
# stay editable without a rebuild, and exercise the bind-mount path).
#
# Env:
#   IMAGE       default artifactflow-sandbox:latest
#   RUNTIME     default runsc  (set RUNTIME=runc for a local rehearsal off-Kylin)
#   PROBE_HOST  / PROBE_NAME   forwarded to verify-network.sh (see that file)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-artifactflow-sandbox:latest}"
RUNTIME="${RUNTIME:-runsc}"
export IMAGE RUNTIME

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "image '$IMAGE' not loaded — docker load it first" >&2; exit 2; }

echo "### ArtifactFlow sandbox §B verification — image=$IMAGE runtime=$RUNTIME"
fails=0
incontainer(){ # title interpreter script
  echo; echo "=== $1 ==="
  docker run --rm --runtime="$RUNTIME" --network=none -v "$HERE:/opt/verify:ro" "$IMAGE" \
    "$2" "/opt/verify/$3" || fails=$((fails + 1))
}

incontainer "ENOSYS (in-container)"          python3 verify-enosys.py
incontainer "pandoc canary (in-container)"   bash    verify-pandoc.sh
incontainer "git local repo (in-container)"  bash    verify-git.sh
incontainer "offline install (in-container)" bash    verify-offline-install.sh

echo; echo "=== bind-mount + uid (host-driven) ==="
bash "$HERE/verify-bindmount.sh" || fails=$((fails + 1))

echo; echo "=== network policy (host-driven) ==="
bash "$HERE/verify-network.sh" || fails=$((fails + 1))

echo
echo "############################################"
if [[ $fails -eq 0 ]]; then
  echo "ALL §B CHECK GROUPS PASSED"
else
  echo "$fails CHECK GROUP(S) FAILED — see above"
fi
[[ $fails -eq 0 ]]
