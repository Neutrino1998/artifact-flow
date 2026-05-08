#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
# Usage: ./scripts/release.sh [VERSION]
# Output: artifactflow-<VERSION>.tar.gz + sha256 checksum

VERSION="${1:-$(date +%Y%m%d)}"
OUTDIR="dist"
ARCHIVE="$OUTDIR/artifactflow-${VERSION}.tar.gz"
CONFIG_ARCHIVE="$OUTDIR/artifactflow-config-${VERSION}.tar.gz"
DEPLOY_ARCHIVE="$OUTDIR/artifactflow-deploy-${VERSION}.tar.gz"

# Build platform — default linux/amd64 because the intranet target is x86_64.
# Apple Silicon Macs default to linux/arm64 without --platform, producing
# images that fail at startup on the server with "exec format error".
# See docs/_archive/intranet部署运维笔记.md → "macOS arm64 → Linux amd64".
PLATFORM="${PLATFORM:-linux/amd64}"

echo "=== ArtifactFlow Release: v${VERSION} (platform: ${PLATFORM}) ==="

# Build application images via buildx so we can cross-compile to amd64.
# `--load` writes the result into the local docker daemon (vs `--push` to a registry).
echo "Building backend image..."
docker buildx build --platform "${PLATFORM}" \
  -t "artifactflow:${VERSION}" -t artifactflow:latest \
  --load .

echo "Building frontend image..."
docker buildx build --platform "${PLATFORM}" \
  -t "artifactflow-frontend:${VERSION}" -t artifactflow-frontend:latest \
  --build-arg NEXT_PUBLIC_API_URL= \
  --load ./frontend

# Collect all images to export
IMAGES=(
  "artifactflow:${VERSION}"
  "artifactflow-frontend:${VERSION}"
  "nginx:1.27-alpine"
  "postgres:16-alpine"
  "redis:7-alpine"
)

# Pull infra images for the target platform. We re-pull when the locally
# cached image is for a different arch (common on Apple Silicon: previous
# `docker pull` left an arm64 cache).
for img in nginx:1.27-alpine postgres:16-alpine redis:7-alpine; do
  current_arch=$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo "missing")
  expected_arch="${PLATFORM##*/}"
  if [[ "$current_arch" != "$expected_arch" ]]; then
    echo "Pulling $img for $PLATFORM (was: $current_arch)..."
    docker pull --platform "$PLATFORM" "$img"
  fi
done

# Export
mkdir -p "$OUTDIR"
echo "Saving images to ${ARCHIVE}..."
docker save "${IMAGES[@]}" | gzip > "$ARCHIVE"

# Package config/ separately so operators can ship prompt / model changes
# without re-transferring the (large) image tarball. The intranet compose
# bind-mounts ../config:/app/config:ro, so config/ must sit next to deploy/
# on the target host.
echo "Packaging config/ to ${CONFIG_ARCHIVE}..."
tar -czf "$CONFIG_ARCHIVE" config/

# Package deploy/ (compose file, nginx.conf, scripts, maintenance assets).
# Three exclusions:
#   - .env / .env.local: secrets, never shipped from build host
#   - maintenance/MAINTENANCE_ON, maintenance/note.txt: runtime state files
#     written by maintenance.sh — shipping a "maintenance ON" flag would put
#     a freshly-deployed host into maintenance mode on first boot.
echo "Packaging deploy/ to ${DEPLOY_ARCHIVE}..."
tar --exclude='deploy/.env' \
    --exclude='deploy/.env.local' \
    --exclude='deploy/maintenance/MAINTENANCE_ON' \
    --exclude='deploy/maintenance/note.txt' \
    -czf "$DEPLOY_ARCHIVE" deploy/

# Checksums — run from inside $OUTDIR so the .sha256 file records the bare
# filename instead of `dist/...`. Otherwise `sha256sum -c` fails on the
# target host where the tar was scp'd into a different directory.
( cd "$OUTDIR" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )
( cd "$OUTDIR" && sha256sum "$(basename "$CONFIG_ARCHIVE")" > "$(basename "$CONFIG_ARCHIVE").sha256" )
( cd "$OUTDIR" && sha256sum "$(basename "$DEPLOY_ARCHIVE")" > "$(basename "$DEPLOY_ARCHIVE").sha256" )

echo ""
echo "=== Release artifacts ==="
ls -lh "$ARCHIVE" "${ARCHIVE}.sha256" \
       "$CONFIG_ARCHIVE" "${CONFIG_ARCHIVE}.sha256" \
       "$DEPLOY_ARCHIVE" "${DEPLOY_ARCHIVE}.sha256"
echo ""
echo "To deploy on air-gapped host (first time):"
echo "  1. Copy three tars (+ sha256) to /opt/artifactflow/ on target:"
echo "       ${ARCHIVE}"
echo "       ${CONFIG_ARCHIVE}"
echo "       ${DEPLOY_ARCHIVE}"
echo "  2. cd /opt/artifactflow"
echo "  3. docker load < $(basename "${ARCHIVE}")"
echo "  4. tar xzf $(basename "${DEPLOY_ARCHIVE}")   # extracts ./deploy"
echo "  5. tar xzf $(basename "${CONFIG_ARCHIVE}")   # extracts ./config"
echo "  6. cp deploy/.env.intranet.example deploy/.env && vi deploy/.env"
echo "  7. AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d"
echo ""
echo "To roll-update an existing deployment (any combination of three tars):"
echo "  scp <tars> target:/opt/artifactflow/"
echo "  ssh target  # then on target:"
echo "    cd /opt/artifactflow"
echo "    docker load < $(basename "${ARCHIVE}")        # if image tar shipped"
echo "    tar xzf $(basename "${DEPLOY_ARCHIVE}")        # if deploy tar shipped"
echo "    tar xzf $(basename "${CONFIG_ARCHIVE}")        # if config tar shipped"
echo "    ./deploy/scripts/pause.sh '升级 v${VERSION}'"
echo "    ./deploy/scripts/resume.sh ${VERSION}"
