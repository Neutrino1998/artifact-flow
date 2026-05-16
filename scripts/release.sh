#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
#
# Usage:
#   ./scripts/release.sh [VERSION] [--with-infra | --app-only]
#
# Defaults:
#   VERSION:     $(date +%Y%m%d)
#   layout:      --app-only (skip infra images)
#
# Output (in dist/):
#   artifactflow-app-<VERSION>.tar.gz          backend + frontend images
#   artifactflow-config-<VERSION>.tar.gz       config/ tree (prompts + site + models)
#   artifactflow-deploy-<VERSION>.tar.gz       deploy/ tree (compose + nginx + scripts)
#   artifactflow-<VERSION>.manifest.txt        human-readable release manifest
#   *.sha256                                    per-tar checksums
#   artifactflow-infra-<infra-slug>.tar.gz     ONLY if --with-infra (content-addressed
#                                              by base image tags; targets that already
#                                              have the same nginx/pg/redis can skip).

show_help() {
  sed -n '1,21p' "$0"
}

VERSION=""
WITH_INFRA=0
for arg in "$@"; do
  case "$arg" in
    --with-infra) WITH_INFRA=1 ;;
    --app-only)   WITH_INFRA=0 ;;
    -h|--help)    show_help; exit 0 ;;
    -*)           echo "Unknown flag: $arg (use -h for usage)" >&2; exit 2 ;;
    *)
      if [[ -n "$VERSION" ]]; then
        echo "Multiple VERSION args given: '$VERSION' and '$arg'" >&2; exit 2
      fi
      VERSION="$arg"
      ;;
  esac
done
VERSION="${VERSION:-$(date +%Y%m%d)}"

OUTDIR="dist"
APP_ARCHIVE="$OUTDIR/artifactflow-app-${VERSION}.tar.gz"
CONFIG_ARCHIVE="$OUTDIR/artifactflow-config-${VERSION}.tar.gz"
DEPLOY_ARCHIVE="$OUTDIR/artifactflow-deploy-${VERSION}.tar.gz"
MANIFEST="$OUTDIR/artifactflow-${VERSION}.manifest.txt"

# Infra base image tags — kept in lockstep with deploy/docker-compose.intranet.yml.
# Content-addressed tar name lets ops see at a glance "do I already have this?"
NGINX_TAG="1.30.1-alpine"
POSTGRES_TAG="16-alpine"
REDIS_TAG="7-alpine"
INFRA_SLUG="nginx${NGINX_TAG%%-*}-pg${POSTGRES_TAG%%-*}-redis${REDIS_TAG%%-*}"
INFRA_ARCHIVE="$OUTDIR/artifactflow-infra-${INFRA_SLUG}.tar.gz"

# Build platform — default linux/amd64 because the intranet target is x86_64.
# Apple Silicon Macs default to linux/arm64 without --platform, producing
# images that fail at startup on the server with "exec format error".
# See docs/_archive/intranet部署运维笔记.md → "macOS arm64 → Linux amd64".
PLATFORM="${PLATFORM:-linux/amd64}"

INFRA_DESC=$([[ $WITH_INFRA == 1 ]] && echo "included" || echo "skipped (--app-only)")
echo "=== ArtifactFlow Release: ${VERSION} (platform: ${PLATFORM}, infra: ${INFRA_DESC}) ==="

mkdir -p "$OUTDIR"

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

APP_IMAGES=(
  "artifactflow:${VERSION}"
  "artifactflow-frontend:${VERSION}"
)

echo "Saving app images to ${APP_ARCHIVE}..."
docker save "${APP_IMAGES[@]}" | gzip > "$APP_ARCHIVE"

if [[ $WITH_INFRA == 1 ]]; then
  INFRA_IMAGES=(
    "nginx:${NGINX_TAG}"
    "postgres:${POSTGRES_TAG}"
    "redis:${REDIS_TAG}"
  )
  # Pull infra images for the target platform. We re-pull when the locally
  # cached image is for a different arch (common on Apple Silicon: previous
  # `docker pull` left an arm64 cache).
  for img in "${INFRA_IMAGES[@]}"; do
    current_arch=$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo "missing")
    expected_arch="${PLATFORM##*/}"
    if [[ "$current_arch" != "$expected_arch" ]]; then
      echo "Pulling $img for $PLATFORM (was: $current_arch)..."
      docker pull --platform "$PLATFORM" "$img"
    fi
  done
  echo "Saving infra images to ${INFRA_ARCHIVE}..."
  docker save "${INFRA_IMAGES[@]}" | gzip > "$INFRA_ARCHIVE"
fi

# Package config/ separately so operators can ship prompt / model changes
# without re-transferring the (larger) image tar. The intranet compose
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
(
  cd "$OUTDIR"
  for f in "$(basename "$APP_ARCHIVE")" \
           "$(basename "$CONFIG_ARCHIVE")" \
           "$(basename "$DEPLOY_ARCHIVE")"; do
    sha256sum "$f" > "$f.sha256"
  done
  if [[ $WITH_INFRA == 1 ]]; then
    f=$(basename "$INFRA_ARCHIVE")
    sha256sum "$f" > "$f.sha256"
  fi
)

# Manifest — single text file capturing what's in this release. Ops can scp it
# alongside the tars to compare against the running deployment without
# untarring anything.
{
  echo "ArtifactFlow Release ${VERSION}"
  echo "Built:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Built from:   $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')@$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "Platform:     ${PLATFORM}"
  LAYOUT_DESC="app + config + deploy"
  [[ $WITH_INFRA == 1 ]] && LAYOUT_DESC+=" + infra"
  echo "Layout:       $LAYOUT_DESC"
  echo ""
  echo "App images:"
  for img in "${APP_IMAGES[@]}"; do
    id=$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null | cut -c8-19)
    size=$(docker image inspect "$img" --format '{{.Size}}' 2>/dev/null \
           | awk '{printf "%.0f MB", $1/1024/1024}')
    echo "  $img"
    echo "    id=$id  size=$size"
  done
  echo ""
  if [[ $WITH_INFRA == 1 ]]; then
    echo "Infra images (in artifactflow-infra-${INFRA_SLUG}.tar.gz):"
    echo "  nginx:${NGINX_TAG}"
    echo "  postgres:${POSTGRES_TAG}"
    echo "  redis:${REDIS_TAG}"
  else
    echo "Infra images: skipped — target must already have these loaded:"
    echo "  nginx:${NGINX_TAG}"
    echo "  postgres:${POSTGRES_TAG}"
    echo "  redis:${REDIS_TAG}"
    echo "  (run release with --with-infra to ship them)"
  fi
  echo ""
  echo "Config tar highlights:"
  # Top-level subdirs + any *.json the operator likely cares about
  tar tzf "$CONFIG_ARCHIVE" \
    | grep -E '^config/[^/]+/$|/notifications\.json$|/welcome_tips\.json$|/models\.yaml$' \
    | sort -u \
    | sed 's/^/  /'
  echo ""
  echo "Deploy tar highlights:"
  tar tzf "$DEPLOY_ARCHIVE" \
    | grep -E '^deploy/[^/]+/$|\.sh$|\.yml$|nginx\.conf$|\.env\.example$' \
    | sort -u \
    | sed 's/^/  /'
} > "$MANIFEST"

echo ""
echo "=== Release artifacts ==="
ls -lh "$OUTDIR"/artifactflow-{app,config,deploy}-"${VERSION}".tar.gz{,.sha256} "$MANIFEST" 2>/dev/null
if [[ $WITH_INFRA == 1 ]]; then
  ls -lh "$INFRA_ARCHIVE" "$INFRA_ARCHIVE.sha256"
fi
echo ""
echo "Manifest preview (first 30 lines):"
head -30 "$MANIFEST" | sed 's/^/  /'
echo ""
cat <<EOF
To deploy on air-gapped host:

  # ---- First-time deployment ----
  # Build must include --with-infra so the infra tar exists.
  scp dist/artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256} \\
      dist/artifactflow-infra-${INFRA_SLUG}.tar.gz{,.sha256}              \\
      dist/artifactflow-${VERSION}.manifest.txt                            \\
      target:/opt/artifactflow/
  ssh target
    cd /opt/artifactflow
    # verify-bundle.sh lives inside deploy/, which isn't extracted yet — use
    # plain sha256sum. Glob is safe in a fresh dir, and CWD matches where
    # each .sha256 records its filename.
    sha256sum -c artifactflow-*.tar.gz.sha256
    tar xzf artifactflow-deploy-${VERSION}.tar.gz
    tar xzf artifactflow-config-${VERSION}.tar.gz
    docker load -i artifactflow-infra-${INFRA_SLUG}.tar.gz
    docker load -i artifactflow-app-${VERSION}.tar.gz
    cp deploy/.env.intranet.example deploy/.env && vi deploy/.env
    AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d
    # No pause/resume here — there's nothing running to pause.

  # ---- Roll-update (most common, no infra) ----
  scp dist/artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256} \\
      dist/artifactflow-${VERSION}.manifest.txt                          \\
      target:/opt/artifactflow/tmp/
  ssh target
    cd /opt/artifactflow
    ./deploy/scripts/verify-bundle.sh tmp
    docker load -i tmp/artifactflow-app-${VERSION}.tar.gz
    tar xzf tmp/artifactflow-deploy-${VERSION}.tar.gz
    tar xzf tmp/artifactflow-config-${VERSION}.tar.gz
    ./deploy/scripts/pause.sh "升级 ${VERSION}"
    ./deploy/scripts/resume.sh ${VERSION}
EOF
