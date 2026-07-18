#!/usr/bin/env bash
set -euo pipefail

# Build one immutable ArtifactFlow application release bundle.
#
# This script is deliberately build-only. Host provisioning stays outside the
# application controller; configuration hotfixes belong to `afctl config`.
#
# Usage:
#   ./scripts/release.sh VERSION [--app-only|--with-infra]
#                                [--platform linux/amd64|linux/arm64]
#
# Output:
#   dist/releases/VERSION/manifest.json
#   dist/releases/VERSION/afctl
#   dist/releases/VERSION/artifactflow-{app,config,deploy,sandbox,...}.tar.gz
#   dist/artifactflow-release-VERSION-ARCH.tar

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION=""
WITH_INFRA=0
PLATFORM="linux/amd64"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-infra) WITH_INFRA=1 ;;
    --app-only) WITH_INFRA=0 ;;
    --platform)
      shift
      [[ $# -gt 0 ]] || { echo "--platform requires a value" >&2; exit 2; }
      PLATFORM="$1"
      ;;
    --platform=*) PLATFORM="${1#--platform=}" ;;
    -h|--help)
      sed -n '3,20p' "$0"
      exit 0
      ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *)
      [[ -z "$VERSION" ]] || { echo "multiple VERSION arguments" >&2; exit 2; }
      VERSION="$1"
      ;;
  esac
  shift
done

[[ -n "$VERSION" ]] || { echo "VERSION is required" >&2; exit 2; }
[[ "$VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || { echo "invalid VERSION: $VERSION" >&2; exit 2; }

case "$PLATFORM" in
  linux/amd64) ARCH=amd64 ;;
  linux/arm64|linux/aarch64) PLATFORM=linux/arm64; ARCH=arm64 ;;
  *) echo "unsupported platform: $PLATFORM" >&2; exit 2 ;;
esac

for command in docker git go python3 tar gzip sha256sum; do
  command -v "$command" >/dev/null 2>&1 \
    || { echo "required build command is missing: $command" >&2; exit 1; }
done

# A release source is a commit, not an accidental mixture of tracked and local
# files. Hotfix config has its own target-side workflow and does not need a
# dirty source release.
git diff --quiet || { echo "working tree has unstaged changes; commit them before release" >&2; exit 1; }
git diff --cached --quiet || { echo "index has staged changes; commit them before release" >&2; exit 1; }
[[ -z "$(git ls-files --others --exclude-standard)" ]] \
  || { echo "working tree has untracked files; commit or ignore them before release" >&2; exit 1; }

OUT_ROOT="$ROOT/dist/releases"
BUNDLE="$OUT_ROOT/$VERSION"
TRANSPORT="$ROOT/dist/artifactflow-release-${VERSION}-${ARCH}.tar"
[[ ! -e "$BUNDLE" && ! -e "$TRANSPORT" ]] \
  || { echo "immutable release output already exists for $VERSION" >&2; exit 1; }

mkdir -p "$OUT_ROOT"
STAGE="$(mktemp -d "$OUT_ROOT/.${VERSION}.tmp.XXXXXX")"
DEPLOY_STAGE="$(mktemp -d "$OUT_ROOT/.${VERSION}.deploy.XXXXXX")"
cleanup() { rm -rf "$STAGE" "$DEPLOY_STAGE"; }
trap cleanup EXIT

APP_ARCHIVE="artifactflow-app-${VERSION}.tar.gz"
CONFIG_ARCHIVE="artifactflow-config-${VERSION}.tar.gz"
DEPLOY_ARCHIVE="artifactflow-deploy-${VERSION}.tar.gz"
SANDBOX_ARCHIVE="artifactflow-sandbox-${VERSION}-${ARCH}.tar.gz"
INFRA_ARCHIVE="artifactflow-infra-caddy2-pg16-redis7-${ARCH}.tar.gz"
content_image_ref() {
  local prefix="$1" source="$2" got id digest ref
  got="$(docker image inspect "$source" --format '{{.Architecture}}' 2>/dev/null || true)"
  if [[ "$got" != "$ARCH" ]]; then
    docker pull --platform "$PLATFORM" "$source" >&2
  fi
  id="$(docker image inspect "$source" --format '{{.Id}}')"
  digest="${id#sha256:}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] \
    || { echo "cannot derive immutable image id for $source" >&2; exit 1; }
  ref="${prefix}:sha256-${digest}"
  docker tag "$source" "$ref"
  printf '%s\n' "$ref"
}

echo "=== ArtifactFlow release $VERSION ($PLATFORM) ==="
echo "→ build afctl for target"
GOCACHE="${GOCACHE:-/tmp/artifactflow-go-build}" \
  GOOS=linux GOARCH="$ARCH" CGO_ENABLED=0 \
  go build -trimpath -ldflags='-s -w' -o "$STAGE/afctl" ./cmd/afctl

echo "→ build content-addressed sandbox image"
PLATFORM="$PLATFORM" scripts/build-sandbox-image.sh "$VERSION"
cp "dist/$SANDBOX_ARCHIVE" "$STAGE/$SANDBOX_ARCHIVE"
SANDBOX_REF_FILE="dist/artifactflow-sandbox-${VERSION}-${ARCH}.image-ref"
[[ -f "$SANDBOX_REF_FILE" ]] || { echo "missing sandbox image ref: $SANDBOX_REF_FILE" >&2; exit 1; }
SANDBOX_IMAGE="$(<"$SANDBOX_REF_FILE")"
[[ "$SANDBOX_IMAGE" =~ ^artifactflow-sandbox:sha256-[0-9a-f]{64}$ ]] \
  || { echo "invalid sandbox image ref: $SANDBOX_IMAGE" >&2; exit 1; }

echo "→ build application images"
docker buildx build --platform "$PLATFORM" \
  -t "artifactflow:${VERSION}" \
  --build-arg "ARTIFACTFLOW_SANDBOX_IMAGE=${SANDBOX_IMAGE}" \
  --load .
docker buildx build --platform "$PLATFORM" \
  -t "artifactflow-frontend:${VERSION}" \
  --build-arg NEXT_PUBLIC_API_URL= \
  --load ./frontend
docker save "artifactflow:${VERSION}" "artifactflow-frontend:${VERSION}" \
  | gzip > "$STAGE/$APP_ARCHIVE"

echo "→ pin infrastructure images by content id"
CADDY_IMAGE="$(content_image_ref artifactflow-caddy caddy:2.10-alpine)"
POSTGRES_IMAGE="$(content_image_ref artifactflow-postgres postgres:16-alpine)"
REDIS_IMAGE="$(content_image_ref artifactflow-redis redis:7-alpine)"

if (( WITH_INFRA )); then
  echo "→ collect infrastructure images"
  docker save "$CADDY_IMAGE" "$POSTGRES_IMAGE" "$REDIS_IMAGE" \
    | gzip > "$STAGE/$INFRA_ARCHIVE"
fi

echo "→ package config"
COPYFILE_DISABLE=1 tar --exclude='.DS_Store' --exclude='._*' \
  -czf "$STAGE/$CONFIG_ARCHIVE" config

echo "→ package deploy unit"
(
  git ls-files -z deploy \
    | COPYFILE_DISABLE=1 tar --null -T - -cf - \
    | tar -xf - -C "$DEPLOY_STAGE"
)
COPYFILE_DISABLE=1 tar --exclude='.DS_Store' --exclude='._*' \
  -czf "$STAGE/$DEPLOY_ARCHIVE" -C "$DEPLOY_STAGE" deploy

SOURCE="$(git rev-parse --abbrev-ref HEAD)@$(git rev-parse HEAD)"
MANIFEST_ARGS=(
  release manifest
  --bundle "$STAGE"
  --id "$VERSION"
  --kind app
  --platform "$PLATFORM"
  --source "$SOURCE"
  --sandbox-image "$SANDBOX_IMAGE"
  --image "artifactflow:${VERSION}"
  --image "artifactflow-frontend:${VERSION}"
  --image "$SANDBOX_IMAGE"
  --image "$CADDY_IMAGE"
  --image "$POSTGRES_IMAGE"
  --image "$REDIS_IMAGE"
  --artifact "app=$APP_ARCHIVE"
  --artifact "config=$CONFIG_ARCHIVE"
  --artifact "deploy=$DEPLOY_ARCHIVE"
  --artifact "sandbox=$SANDBOX_ARCHIVE"
)
if (( WITH_INFRA )); then MANIFEST_ARGS+=(--artifact "infra=$INFRA_ARCHIVE"); fi

echo "→ finalize strict manifest"
GOCACHE="${GOCACHE:-/tmp/artifactflow-go-build}" \
  go run ./cmd/afctl "${MANIFEST_ARGS[@]}"

mv "$STAGE" "$BUNDLE"
trap - EXIT
rm -rf "$DEPLOY_STAGE"

# A single uncompressed outer tar avoids recompressing the already-compressed
# image archives. Extract it on the control host, then pass the directory to
# afctl. manifest.json remains the only deployment contract.
tar -cf "$TRANSPORT" -C "$OUT_ROOT" "$VERSION"
(
  cd "$(dirname "$TRANSPORT")"
  TRANSPORT_NAME="$(basename "$TRANSPORT")"
  sha256sum "$TRANSPORT_NAME" > "$TRANSPORT_NAME.sha256"
)

echo
echo "✓ release bundle: $BUNDLE"
echo "✓ transport archive: $TRANSPORT"
echo
echo "Target workflow:"
echo "  tar xf $(basename "$TRANSPORT")"
echo "  sudo ./$VERSION/afctl --root /opt/artifactflow site init --preset intranet  # first host only"
echo "  # edit control/site.toml + control/.env and provision documented host prerequisites"
echo "  sudo ./$VERSION/afctl --root /opt/artifactflow doctor"
echo "  sudo ./$VERSION/afctl --root /opt/artifactflow plan apply ./$VERSION"
echo "  sudo ./$VERSION/afctl --root /opt/artifactflow apply ./$VERSION"
echo "  sudo install -m 0755 ./$VERSION/afctl /opt/artifactflow/bin/afctl  # after successful apply"
