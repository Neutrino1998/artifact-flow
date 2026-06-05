#!/usr/bin/env bash
set -euo pipefail

# Install gVisor runsc + containerd-shim from this offline package and register
# runsc as a Docker runtime. Run as root on the target (Kylin) node.
#
# Does NOT reload docker — the caller runs `systemctl reload docker` afterward
# (reload, not restart, so running containers are not disturbed). See README.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/bin"
DEST=/usr/local/bin
DAEMON_JSON=/etc/docker/daemon.json

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "must run as root" >&2; exit 1; }

echo "→ verifying checksums..."
( cd "$BIN" && sha512sum -c runsc.sha512 containerd-shim-runsc-v1.sha512 )

echo "→ installing binaries to $DEST..."
install -m 0755 "$BIN/runsc" "$DEST/runsc"
install -m 0755 "$BIN/containerd-shim-runsc-v1" "$DEST/containerd-shim-runsc-v1"

echo "→ registering runsc in $DAEMON_JSON (merge, don't clobber existing keys)..."
mkdir -p /etc/docker
python3 - "$DAEMON_JSON" "$DEST/runsc" <<'PY'
import json, os, sys
path, runsc = sys.argv[1], sys.argv[2]
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        txt = f.read().strip()
        if txt:
            cfg = json.loads(txt)
cfg.setdefault("runtimes", {})["runsc"] = {"path": runsc}
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("  runtimes.runsc ->", runsc)
PY

echo
echo "✓ installed. Next:"
echo "    sudo systemctl reload docker      # reload, not restart"
echo "    sudo $HERE/smoke-test.sh          # 5-tier progressive smoke"
