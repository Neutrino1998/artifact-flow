#!/usr/bin/env bash
set -euo pipefail

# Build host (NETWORKED): download the Docker Engine STATIC binaries + the
# docker compose plugin for the target arch, and assemble an offline install
# package for a BARE air-gapped node (e.g. fresh Kylin V10 arm with nothing on
# it — no docker, no package mirror). Static binaries are the cleanest air-gap
# path: one tarball, no distro package/dependency resolution.
#
# Usage:
#   sandbox/docker-pkg/fetch-and-package.sh
#   ARCH=aarch64 sandbox/docker-pkg/fetch-and-package.sh        # Kylin arm
#   DOCKER_VERSION=27.5.1 COMPOSE_VERSION=v2.32.4 ARCH=aarch64 sandbox/docker-pkg/fetch-and-package.sh
#
# ARCH is the docker.com / compose-release arch token: x86_64 | aarch64.

DOCKER_VERSION="${DOCKER_VERSION:-27.5.1}"
COMPOSE_VERSION="${COMPOSE_VERSION:-v2.32.4}"
ARCH="${ARCH:-x86_64}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUTDIR="$ROOT/dist"
STAMP="$(date +%Y%m%d)"
STAGE="$OUTDIR/docker-offline-${STAMP}-${ARCH}"
TAR="$OUTDIR/docker-offline-${STAMP}-${ARCH}.tar.gz"

DOCKER_URL="https://download.docker.com/linux/static/stable/${ARCH}/docker-${DOCKER_VERSION}.tgz"
COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}"

mkdir -p "$STAGE/bin"

echo "=== Docker offline package: engine ${DOCKER_VERSION} + compose ${COMPOSE_VERSION} (${ARCH}) ==="
echo "→ downloading docker static bundle..."
curl -fSL "$DOCKER_URL" -o "$STAGE/docker-${DOCKER_VERSION}.tgz"
echo "→ downloading compose plugin..."
curl -fSL "$COMPOSE_URL" -o "$STAGE/bin/docker-compose"
chmod 0755 "$STAGE/bin/docker-compose"

echo "→ computing our own SHA256SUMS (upstream static bundle ships no per-arch sum)..."
( cd "$STAGE" && sha256sum "docker-${DOCKER_VERSION}.tgz" bin/docker-compose > SHA256SUMS )

echo "→ assembling package (scripts from repo)..."
cp "$HERE/install.sh" "$HERE/uninstall.sh" "$HERE/README.md" "$STAGE/"
chmod +x "$STAGE"/*.sh
cat > "$STAGE/VERSION" <<EOF
docker-engine ${DOCKER_VERSION}, compose ${COMPOSE_VERSION}, ${ARCH}
EOF

tar -czf "$TAR" -C "$OUTDIR" "docker-offline-${STAMP}-${ARCH}"
( cd "$OUTDIR" && sha256sum "$(basename "$TAR")" > "$(basename "$TAR").sha256" )
rm -rf "$STAGE"

echo
echo "✓ $TAR"
echo "✓ $TAR.sha256"
echo
echo "On the bare node (root): tar xzf $(basename "$TAR") && cd docker-offline-${STAMP}-${ARCH} && sudo ./install.sh"
