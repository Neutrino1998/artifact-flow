#!/usr/bin/env bash
set -euo pipefail

# Build host (NETWORKED): download gVisor runsc + containerd-shim, verify the
# upstream sha512, and assemble the offline install package that the intranet
# Kylin node uses. The binaries (~46MB) are NOT in git; this script reproduces
# the tar. The install/smoke/uninstall scripts come from THIS dir (in repo), so
# the package is fully reconstructable — the old hand-built tar that got deleted
# is no longer a single point of loss.
#
# Usage:
#   sandbox/gvisor-pkg/fetch-and-package.sh
#   GVISOR_VERSION=20260504.0 ARCH=x86_64 sandbox/gvisor-pkg/fetch-and-package.sh
#
# Version pinned to the one validated in the eval doc (§3): release-20260504.0.

GVISOR_VERSION="${GVISOR_VERSION:-20260504.0}"
ARCH="${ARCH:-x86_64}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUTDIR="$ROOT/dist"
BASEURL="https://storage.googleapis.com/gvisor/releases/release/${GVISOR_VERSION}/${ARCH}"
STAMP="$(date +%Y%m%d)"
# arch in the name so x86_64 + aarch64 packages coexist in dist/ (a target node
# is single-arch — the gVisor binary is arch-specific, unlike the verify probes).
STAGE="$OUTDIR/sandbox-gvisor-${STAMP}-${ARCH}"
TAR="$OUTDIR/sandbox-gvisor-${STAMP}-${ARCH}.tar.gz"

mkdir -p "$STAGE/bin"

echo "=== gVisor offline package: release-${GVISOR_VERSION} (${ARCH}) ==="
echo "→ downloading runsc + shim (+ upstream .sha512)..."
for f in runsc runsc.sha512 containerd-shim-runsc-v1 containerd-shim-runsc-v1.sha512; do
  curl -fsSL "$BASEURL/$f" -o "$STAGE/bin/$f"
done

echo "→ verifying upstream sha512..."
( cd "$STAGE/bin" && sha512sum -c runsc.sha512 containerd-shim-runsc-v1.sha512 )
chmod 0755 "$STAGE/bin/runsc" "$STAGE/bin/containerd-shim-runsc-v1"

echo "→ assembling package (scripts from repo)..."
cp "$HERE/install.sh" "$HERE/smoke-test.sh" "$HERE/uninstall.sh" "$HERE/README.md" "$STAGE/"
chmod +x "$STAGE"/*.sh
echo "release-${GVISOR_VERSION}, ${ARCH}" > "$STAGE/VERSION"

tar -czf "$TAR" -C "$OUTDIR" "sandbox-gvisor-${STAMP}-${ARCH}"
( cd "$OUTDIR" && sha256sum "$(basename "$TAR")" > "$(basename "$TAR").sha256" )
rm -rf "$STAGE"

echo
echo "✓ $TAR"
echo "✓ $TAR.sha256"
echo
echo "Carry to the intranet node, then: tar xzf $(basename "$TAR") && cd sandbox-gvisor-${STAMP}-${ARCH} && sudo ./install.sh"
