#!/usr/bin/env bash
set -euo pipefail

# Remove the offline-installed Docker Engine ("验完即撤"). Run as root.
# Binaries + units only by default; pass PURGE=1 to also wipe /var/lib/docker
# and /var/lib/containerd (all images/containers/volumes — irreversible).

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "must run as root" >&2; exit 1; }
DEST=/usr/local/bin

echo "→ stopping + disabling services..."
systemctl disable --now docker docker.socket containerd 2>/dev/null || true

echo "→ removing systemd units..."
rm -f /etc/systemd/system/docker.service /etc/systemd/system/docker.socket \
      /etc/systemd/system/containerd.service
systemctl daemon-reload

echo "→ removing binaries..."
for b in dockerd docker docker-init docker-proxy containerd containerd-shim-runc-v2 \
         ctr runc docker-compose; do
  rm -f "$DEST/$b"
done
rm -f /usr/local/lib/docker/cli-plugins/docker-compose

if [[ "${PURGE:-0}" == "1" ]]; then
  echo "→ PURGE=1: wiping /var/lib/docker + /var/lib/containerd ..."
  rm -rf /var/lib/docker /var/lib/containerd /run/docker.sock
fi

echo "✓ docker removed${PURGE:+ (data purged)}."
