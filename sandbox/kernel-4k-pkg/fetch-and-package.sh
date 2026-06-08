#!/usr/bin/env bash
set -euo pipefail
# Build host (NETWORKED): download the Kylin V10 SP3 4K-page kernel RPMs, checksum
# them, and assemble the offline package the air-gapped Kylin arm node installs to
# switch a 64K-page kernel → 4K. gVisor (runsc) on arm64 REQUIRES 4K base pages;
# Kylin V10 arm ships 64K by default, which blocks Sentry ("non-4K host"). The 4K
# kernel is a drop-in RPM set the vendor ships separately (no extra ISO).
#
# Binaries (~63MB) are NOT in git; this script reproduces the tar. The
# preflight/install/postcheck/README come from THIS dir (in repo), so the package
# is fully reconstructable.
#
# Usage:
#   sandbox/kernel-4k-pkg/fetch-and-package.sh
#   BUILD=89.38 sandbox/kernel-4k-pkg/fetch-and-package.sh
#
# aarch64 only — x86 Kylin already runs 4K pages, so it has no 4K kernel repo.

BUILD="${BUILD:-89.38}"
BASEURL="https://update.cs2c.com.cn/CS/V10/V10SP3-2403/kernel-4k/aarch64/Packages"
VER="4.19.90-${BUILD}.4k.v2401.ky10.aarch64"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUTDIR="$ROOT/dist"
STAMP="$(date +%Y%m%d)"
NAME="sandbox-kernel4k-${STAMP}-${BUILD}"
STAGE="$OUTDIR/$NAME"
TAR="$OUTDIR/$NAME.tar.gz"

mkdir -p "$STAGE"
echo "=== Kylin V10 SP3 4K kernel offline package: $VER ==="
echo "→ downloading boot-essential RPMs (core/modules/modules-extra/meta)..."
# Only these four boot a kernel; -devel/-headers/-tools/bpftool/perf are build-time.
for p in kernel-core kernel-modules kernel-modules-extra kernel; do
  f="${p}-${VER}.rpm"
  curl -fsSL "$BASEURL/$f" -o "$STAGE/$f"
done

echo "→ computing SHA256SUMS..."
( cd "$STAGE" && sha256sum *.rpm > SHA256SUMS )

echo "→ assembling package (scripts from repo)..."
cp "$HERE/preflight.sh" "$HERE/install.sh" "$HERE/postcheck.sh" "$HERE/README.md" "$STAGE/"
chmod +x "$STAGE"/*.sh
echo "$VER" > "$STAGE/VERSION"

tar -czf "$TAR" -C "$OUTDIR" "$NAME"
( cd "$OUTDIR" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )
rm -rf "$STAGE"

echo
echo "✓ $TAR"
echo "✓ $TAR.sha256"
echo
echo "Carry to the Kylin arm node, then: tar xzf $NAME.tar.gz && cd $NAME && ./preflight.sh"
