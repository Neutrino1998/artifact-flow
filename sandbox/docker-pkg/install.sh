#!/usr/bin/env bash
set -euo pipefail

# Install Docker Engine from the offline STATIC bundle on a BARE node (root).
# Mirrors docker's "install from binaries" + the moby contrib systemd units.
# Idempotent-ish: refuses to clobber an existing dockerd unless FORCE=1.
#
# After this, install gVisor (sandbox/gvisor-pkg/install.sh) then
# `systemctl reload docker`. This script does NOT install runsc.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/usr/local/bin
CLIPLUGINS=/usr/local/lib/docker/cli-plugins

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "must run as root" >&2; exit 1; }

if command -v dockerd >/dev/null 2>&1 && [[ "${FORCE:-0}" != "1" ]]; then
  echo "dockerd already present ($(command -v dockerd)). Set FORCE=1 to reinstall." >&2
  exit 1
fi

echo "→ verifying checksums..."
( cd "$HERE" && sha256sum -c SHA256SUMS )

DOCKER_TGZ="$(ls "$HERE"/docker-*.tgz | head -1)"
echo "→ extracting $(basename "$DOCKER_TGZ") → $DEST ..."
tmp="$(mktemp -d)"
tar -xzf "$DOCKER_TGZ" -C "$tmp"          # → $tmp/docker/{dockerd,docker,containerd,runc,ctr,...}
install -m 0755 "$tmp"/docker/* "$DEST"/
rm -rf "$tmp"

echo "→ installing compose plugin → $CLIPLUGINS/docker-compose ..."
install -d "$CLIPLUGINS"
install -m 0755 "$HERE/bin/docker-compose" "$CLIPLUGINS/docker-compose"
ln -sf "$CLIPLUGINS/docker-compose" "$DEST/docker-compose"   # also support `docker-compose`

echo "→ creating docker group + /etc/docker ..."
getent group docker >/dev/null 2>&1 || groupadd --system docker
mkdir -p /etc/docker

echo "→ writing systemd units ..."
cat > /etc/systemd/system/containerd.service <<'UNIT'
[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target local-fs.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=1048576
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/docker.socket <<'UNIT'
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/var/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
UNIT

cat > /etc/systemd/system/docker.service <<'UNIT'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target docker.socket containerd.service
Wants=network-online.target containerd.service
Requires=docker.socket

[Service]
Type=notify
ExecStart=/usr/local/bin/dockerd -H fd:// --containerd=/run/containerd/containerd.sock
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

echo "→ enabling + starting (containerd, docker)..."
systemctl daemon-reload
systemctl enable --now containerd
systemctl enable --now docker.socket
systemctl enable --now docker

echo
echo "✓ docker installed. Verify:"
echo "    docker info | grep -i 'server version'"
echo "    docker compose version"
echo
echo "Kylin V10 gotchas if dockerd won't start (see README):"
echo "  - SELinux enforcing: 'setenforce 0' (+ persist) or add policy"
echo "  - missing 'overlay' module / iptables: check 'journalctl -u docker'"
echo
echo "Next: install gVisor (sandbox/gvisor-pkg/install.sh) then 'systemctl reload docker'."
