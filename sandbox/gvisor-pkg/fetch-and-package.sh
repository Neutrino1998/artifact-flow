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
#   GVISOR_VERSION=20260706.0 ARCH=x86_64 sandbox/gvisor-pkg/fetch-and-package.sh
#
# Version pinned to the verified ARM intranet deployment artifact.
# The package name is version+arch addressed so the same verified runtime bundle
# is reused across application releases instead of being downloaded each time.

GVISOR_VERSION="${GVISOR_VERSION:-20260706.0}"
ARCH="${ARCH:-x86_64}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUTDIR="$ROOT/dist"
BASEURL="https://storage.googleapis.com/gvisor/releases/release/${GVISOR_VERSION}/${ARCH}"
# arch in the name so x86_64 + aarch64 packages coexist in dist/ (a target node
# is single-arch — the gVisor binary is arch-specific, unlike the verify probes).
PACKAGE="sandbox-gvisor-release-${GVISOR_VERSION}-${ARCH}"
STAGE="$OUTDIR/$PACKAGE"
TAR="$OUTDIR/$PACKAGE.tar.gz"
RECIPE_SHA="$({
  for source in "$HERE/fetch-and-package.sh" "$HERE/install.sh" "$HERE/smoke-test.sh" "$HERE/uninstall.sh" "$HERE/README.md"; do
    shasum -a 256 "$source"
  done
} | shasum -a 256 | awk '{print $1}')"
PACKAGED_RECIPE_SHA=""
if [[ -f "$TAR" ]]; then
  PACKAGED_RECIPE_SHA="$(tar -xOf "$TAR" "$PACKAGE/PACKAGE-RECIPE.sha256" 2>/dev/null || true)"
fi

if [[ -f "$TAR" && -f "$TAR.sha256" ]] \
    && [[ "$PACKAGED_RECIPE_SHA" == "$RECIPE_SHA" ]] \
    && ( cd "$OUTDIR" && sha256sum -c "$(basename "$TAR").sha256" >/dev/null 2>&1 ); then
  echo "✓ reusing verified $TAR"
  exit 0
fi

rm -rf "$STAGE"
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
echo "$RECIPE_SHA" > "$STAGE/PACKAGE-RECIPE.sha256"

tar -czf "$TAR" -C "$OUTDIR" "$PACKAGE"
( cd "$OUTDIR" && sha256sum "$(basename "$TAR")" > "$(basename "$TAR").sha256" )
rm -rf "$STAGE"

echo
echo "✓ $TAR"
echo "✓ $TAR.sha256"
echo
echo "Carry to the intranet node, then: tar xzf $(basename "$TAR") && cd $PACKAGE && sudo ./install.sh"
