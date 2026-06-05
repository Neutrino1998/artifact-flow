#!/bin/bash
set -euo pipefail

# Build + save the ArtifactFlow sandbox image for air-gapped gVisor verification
# (plan §B). Mirrors scripts/release.sh's buildx → save → sha256 → manifest flow.
#
# Usage:
#   ./scripts/build-sandbox-image.sh [VERSION]
#
# Defaults:
#   VERSION: $(date +%Y%m%d)
#
# Output (dist/):
#   artifactflow-sandbox-<VERSION>.tar.gz         docker-saved image (gzip)
#   artifactflow-sandbox-<VERSION>.tar.gz.sha256  checksum (bare filename — see
#                                                 deploy/scripts/verify-bundle.sh)
#   artifactflow-sandbox-<VERSION>.wheels.lock    resolved pip set baked in the
#                                                 image (diff-friendly sidecar)
#   artifactflow-sandbox-<VERSION>.manifest.txt   image id + tool versions
#
# Air-gap contract: everything is fetched on THIS (networked) build host and
# baked into the saved tar. The intranet test node does `docker load` and runs
# under --runtime=runsc --network=none with ZERO network (plan 原则 7).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTX="$ROOT/sandbox"
OUTDIR="$ROOT/dist"

VERSION="${1:-$(date +%Y%m%d)}"

# Target arch. Default linux/amd64 (x86_64 intranet). For Kylin arm (Kunpeng)
# pass PLATFORM=linux/arm64. On Apple Silicon, arm64 builds NATIVE (fast) while
# amd64 is QEMU-emulated and SLOW (several min, occasionally 10min+). A mid-pull
# SSL/EOF against deb.debian.org/PyPI is the build-host proxy flapping (memory:
# release-build-proxy-flap), NOT the Dockerfile — re-run, buildx cache is fast.
PLATFORM="${PLATFORM:-linux/amd64}"
case "$PLATFORM" in
  *amd64)         ARCH_TAG=amd64 ;;
  *arm64|*aarch64) ARCH_TAG=arm64 ;;
  *)              ARCH_TAG="${PLATFORM##*/}" ;;
esac

# Arch-suffixed names so amd64 + arm64 artifacts coexist in dist/ (a Kylin box is
# single-arch — loading one tar is unambiguous). The verify tar is arch-AGNOSTIC
# (Python/bash probes) → shared, no suffix.
IMAGE="artifactflow-sandbox:${VERSION}-${ARCH_TAG}"
ARCHIVE="$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.tar.gz"
VERIFY_ARCHIVE="$OUTDIR/artifactflow-sandbox-verify-${VERSION}.tar.gz"
LOCK="$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.wheels.lock"
MANIFEST="$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.manifest.txt"

mkdir -p "$OUTDIR"

echo "=== ArtifactFlow sandbox image: ${VERSION} (platform: ${PLATFORM}, tag: ${ARCH_TAG}) ==="
echo "Building ${IMAGE} (native if build-host arch == ${ARCH_TAG}, else QEMU — be patient)..."
docker buildx build --platform "${PLATFORM}" \
  -t "${IMAGE}" -t artifactflow-sandbox:latest \
  --load "$CTX"

# Pull the frozen pip set + tool versions OUT of the built image so ops can
# inspect/diff without loading the (large) tar. -u 0 reads /opt regardless of
# the image's default non-root USER.
echo "Extracting baked wheels.lock + tool versions..."
docker run --rm -u 0 "${IMAGE}" cat /opt/sandbox-wheels.lock.txt > "$LOCK"
PY_VER=$(docker run --rm "${IMAGE}" python3 --version)
PANDOC_VER=$(docker run --rm "${IMAGE}" pandoc --version | head -1)
RG_VER=$(docker run --rm "${IMAGE}" rg --version | head -1)
# Locally-built --load images have no RepoDigests (those come from a registry);
# .Id (the config digest) is the right freeze anchor for an air-gapped image.
IMAGE_ID=$(docker image inspect "${IMAGE}" --format '{{.Id}}')

echo "Saving image to ${ARCHIVE}..."
# Save BOTH the versioned tag AND :latest so that after `docker load` on the
# target, the default tag in smoke-test.sh / run-all.sh (:latest) resolves —
# otherwise the loaded image only has :<VERSION> and the defaults miss it.
docker save "${IMAGE}" artifactflow-sandbox:latest | gzip > "$ARCHIVE"
# Checksum with a bare filename (run inside dist/) so `sha256sum -c` works from
# that dir — same convention as release.sh / verify-bundle.sh.
( cd "$OUTDIR" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )

# Package the verify probes as a third transfer unit. They are NOT baked into
# the image (kept editable + the host-side probes — bindmount/network/run-all —
# must run on the host, not in a container), so they must ride their own tar to
# the air-gapped node. --no-xattrs/--no-fflags + .DS_Store/__pycache__ excludes
# mirror release.sh's tar hygiene.
echo "Packaging verify probes to ${VERIFY_ARCHIVE}..."
tar --no-xattrs --no-fflags --exclude='.DS_Store' --exclude='__pycache__' \
    -czf "$VERIFY_ARCHIVE" -C "$ROOT/sandbox" verify
( cd "$OUTDIR" && sha256sum "$(basename "$VERIFY_ARCHIVE")" > "$(basename "$VERIFY_ARCHIVE").sha256" )

WHEEL_COUNT=$(wc -l < "$LOCK" | tr -d ' ')
cat > "$MANIFEST" <<EOF
ArtifactFlow sandbox image — ${VERSION}
Built (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)
Platform:    ${PLATFORM}
Image id:    ${IMAGE_ID}

Tools:
  ${PY_VER}
  ${PANDOC_VER}
  ${RG_VER}

Python deps: artifactflow-sandbox-${VERSION}.wheels.lock (${WHEEL_COUNT} pkgs)

Role: tier-1 baked sandbox environment for gVisor (runsc) verification (plan §B).
Decoupled from the backend requirements.lock — this is the sandbox runtime, not the app.

Deploy on the intranet test node (zero network):
  gunzip -c $(basename "$ARCHIVE") | docker load
  # then run the §B probes under runsc, e.g.:
  docker run --rm --runtime=runsc --network=none \\
    -v "\$PWD/sandbox/verify:/opt/verify:ro" ${IMAGE} \\
    python3 /opt/verify/verify-enosys.py
EOF

echo
echo "✓ Done:"
echo "  $ARCHIVE"
echo "  $ARCHIVE.sha256"
echo "  $VERIFY_ARCHIVE (+ .sha256)"
echo "  $LOCK"
echo "  $MANIFEST"
echo
echo "Carry to the intranet node: the image tar, the verify tar, and the gVisor"
echo "package tar (sandbox/gvisor-pkg/fetch-and-package.sh). Then docker load the"
echo "image, tar xzf the verify tar, and run verify/run-all.sh under runsc."
