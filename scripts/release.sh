#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
# Usage: ./scripts/release.sh [VERSION]
# Output: artifactflow-<VERSION>.tar.gz + sha256 checksum

VERSION="${1:-$(date +%Y%m%d)}"
OUTDIR="dist"
ARCHIVE="$OUTDIR/artifactflow-${VERSION}.tar.gz"
CONFIG_ARCHIVE="$OUTDIR/artifactflow-config-${VERSION}.tar.gz"

echo "=== ArtifactFlow Release: v${VERSION} ==="

# Build application images
echo "Building backend image..."
docker build -t "artifactflow:${VERSION}" -t artifactflow:latest .

echo "Building frontend image..."
docker build -t "artifactflow-frontend:${VERSION}" -t artifactflow-frontend:latest \
  --build-arg NEXT_PUBLIC_API_URL= \
  ./frontend

# Collect all images to export
IMAGES=(
  "artifactflow:${VERSION}"
  "artifactflow-frontend:${VERSION}"
  "nginx:1.27-alpine"
  "postgres:16-alpine"
  "redis:7-alpine"
)

# Pull infra images if not present
for img in nginx:1.27-alpine postgres:16-alpine redis:7-alpine; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull "$img"
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

# Checksums
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"
sha256sum "$CONFIG_ARCHIVE" > "${CONFIG_ARCHIVE}.sha256"

echo ""
echo "=== Release artifacts ==="
ls -lh "$ARCHIVE" "${ARCHIVE}.sha256" "$CONFIG_ARCHIVE" "${CONFIG_ARCHIVE}.sha256"
echo ""
echo "To deploy on air-gapped host:"
echo "  1. Copy to /opt/artifactflow/ on target:"
echo "       ${ARCHIVE}"
echo "       ${CONFIG_ARCHIVE}"
echo "       deploy/   (whole directory)"
echo "  2. cd /opt/artifactflow"
echo "  3. docker load < $(basename "${ARCHIVE}")"
echo "  4. tar xzf $(basename "${CONFIG_ARCHIVE}")   # extracts ./config"
echo "  5. cp deploy/.env.intranet.example deploy/.env && vi deploy/.env"
echo "  6. AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d"
echo ""
echo "To ship config-only updates later (no image re-transfer):"
echo "  scp ${CONFIG_ARCHIVE} target:/opt/artifactflow/"
echo "  ssh target 'cd /opt/artifactflow && tar xzf $(basename "${CONFIG_ARCHIVE}") \\"
echo "              && docker compose -f deploy/docker-compose.intranet.yml restart backend'"
