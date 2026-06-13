#!/usr/bin/env bash
set -euo pipefail

# Remove runsc + containerd-shim and de-register the runsc runtime from
# daemon.json. Run as root. "验完即撤出" — the B-phase withdrawal step.
# Does NOT reload docker — caller runs `systemctl reload docker` afterward.

DEST=/usr/local/bin
DAEMON_JSON=/etc/docker/daemon.json

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "must run as root" >&2; exit 1; }

echo "→ removing binaries..."
rm -f "$DEST/runsc" "$DEST/containerd-shim-runsc-v1"

if [[ -f "$DAEMON_JSON" ]]; then
  echo "→ de-registering runsc from $DAEMON_JSON..."
  python3 - "$DAEMON_JSON" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
cfg.get("runtimes", {}).pop("runsc", None)
if not cfg.get("runtimes"):
    cfg.pop("runtimes", None)
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PY
fi

echo "✓ runsc removed. Run: sudo systemctl reload docker"
