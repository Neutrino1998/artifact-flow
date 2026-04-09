#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
# Usage: ./scripts/release.sh [VERSION]
# Output: artifactflow-<VERSION>.tar.gz + sha256 checksum

VERSION="${1:-$(date +%Y%m%d)}"
OUTDIR="dist"
ARCHIVE="$OUTDIR/artifactflow-${VERSION}.tar.gz"

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

# Checksum
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"

echo ""
echo "=== Release artifacts ==="
ls -lh "$ARCHIVE" "${ARCHIVE}.sha256"
echo ""
echo "To deploy on air-gapped host:"
echo "  1. Copy ${ARCHIVE} and deploy/ directory to target"
echo "  2. docker load < ${ARCHIVE}"
echo "  3. cp deploy/.env.intranet.example deploy/.env && vi deploy/.env"
echo "  4. AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d"
