#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
#
# Usage:
#   ./scripts/release.sh [VERSION] [--with-infra | --app-only]
#                        [--with-sandbox] [--with-analyst-tools]
#                        [--platform linux/amd64|linux/arm64]
#                        [--resume] [--force PHASE]
#
# Defaults:
#   VERSION:     $(date +%Y%m%d)
#   layout:      --app-only (skip infra images, skip analyst tools)
#
# Output (in dist/):
#   artifactflow-app-<VERSION>.tar.gz             backend + frontend images
#   artifactflow-config-<VERSION>.tar.gz          config/ tree (prompts + site + models)
#   artifactflow-deploy-<VERSION>.tar.gz          deploy/ tree (compose + Caddyfiles + scripts)
#   artifactflow-<VERSION>.manifest.txt           human-readable release manifest
#   *.sha256                                       per-tar checksums
#   artifactflow-infra-<infra-slug>.tar.gz        ONLY if --with-infra (content-
#                                                  addressed by base image tags).
#   artifactflow-sandbox-<VERSION>-<arch>.tar.gz  ONLY if --with-sandbox
#   artifactflow-sandbox-verify-<VERSION>.tar.gz  ONLY if --with-sandbox
#   sandbox-gvisor-<date>-<arch>.tar.gz           ONLY if --with-sandbox
#   artifactflow-analyst-tools-<slug>.tar.gz      ONLY if --with-analyst-tools
#                                                  (slug encodes pandas + numpy +
#                                                  python versions, NECESSARY for
#                                                  identity; wheels.lock.txt
#                                                  inside the tar is the SUFFICIENT
#                                                  equivalence check). Offline
#                                                  pandas/numpy wheels for
#                                                  scripts/observability_report.py.
#
# NB: py-spy used to ship in this bundle but now lives inside the backend image
# (Dockerfile builder stage) + compose cap_add: [SYS_PTRACE]. See
# docs/_archive/ops/incident-2026-05-14-fix-plan.md → PR-forensics-bundle.
#
# Air-gap contract:
#   Everything downloaded by this script is downloaded on the BUILD host.
#   Target intranet hosts MUST be able to deploy with zero network calls
#   (no `pip install <pkgname>` against PyPI, no `curl github`, etc.). All
#   transitive dependencies of pandas/numpy are pre-downloaded into the
#   analyst-tools tar so `pip install --no-index --find-links wheels pandas`
#   resolves offline.

show_help() {
  sed -n '5,49p' "$0"
}

VERSION=""
WITH_INFRA=0
WITH_SANDBOX=0
WITH_ANALYST_TOOLS=0
PLATFORM_ARG=""
RESUME=0
FORCE_PHASES=()
while [[ $# -gt 0 ]]; do
  arg="$1"
  case "$arg" in
    --with-infra)         WITH_INFRA=1 ;;
    --app-only)           WITH_INFRA=0 ;;
    --with-sandbox)       WITH_SANDBOX=1 ;;
    --with-analyst-tools) WITH_ANALYST_TOOLS=1 ;;
    --resume)             RESUME=1 ;;
    --force)
      shift
      [[ $# -gt 0 ]] || { echo "--force requires a phase name" >&2; exit 2; }
      FORCE_PHASES+=("$1")
      ;;
    --force=*)            FORCE_PHASES+=("${arg#--force=}") ;;
    --platform)
      shift
      [[ $# -gt 0 ]] || { echo "--platform requires a value (e.g. linux/amd64)" >&2; exit 2; }
      PLATFORM_ARG="$1"
      ;;
    --platform=*)         PLATFORM_ARG="${arg#--platform=}" ;;
    -h|--help)            show_help; exit 0 ;;
    -*)                   echo "Unknown flag: $arg (use -h for usage)" >&2; exit 2 ;;
    *)
      if [[ -n "$VERSION" ]]; then
        echo "Multiple VERSION args given: '$VERSION' and '$arg'" >&2; exit 2
      fi
      VERSION="$arg"
      ;;
  esac
  shift
done
VERSION="${VERSION:-$(date +%Y%m%d)}"

# Build platform — default linux/amd64 because the intranet target is x86_64.
# Apple Silicon Macs default to linux/arm64 without --platform, producing
# images that fail at startup on the server with "exec format error".
# See docs/_archive/intranet部署运维笔记.md → "macOS arm64 → Linux amd64".
PLATFORM_INPUT="${PLATFORM_ARG:-${PLATFORM:-linux/amd64}}"
case "$PLATFORM_INPUT" in
  linux/amd64) PLATFORM=linux/amd64; ARCH_TAG=amd64; GVISOR_ARCH=x86_64 ;;
  linux/arm64|linux/aarch64) PLATFORM=linux/arm64; ARCH_TAG=arm64; GVISOR_ARCH=aarch64 ;;
  *) echo "Unsupported platform '$PLATFORM_INPUT' (expected linux/amd64 or linux/arm64)" >&2; exit 2 ;;
esac
SANDBOX_IMAGE_REF="$(python3 "$ROOT/scripts/sandbox_runtime_ref.py" --arch "$ARCH_TAG")"

OUTDIR="dist"
APP_ARCHIVE="$OUTDIR/artifactflow-app-${VERSION}.tar.gz"
CONFIG_ARCHIVE="$OUTDIR/artifactflow-config-${VERSION}.tar.gz"
DEPLOY_ARCHIVE="$OUTDIR/artifactflow-deploy-${VERSION}.tar.gz"
MANIFEST="$OUTDIR/artifactflow-${VERSION}.manifest.txt"
STATE_DIR="$OUTDIR/.release-state/${VERSION}-${ARCH_TAG}"

# Infra base image tags — kept in lockstep with deploy/docker-compose.intranet.yml.
# Content-addressed tar name lets ops see at a glance "do I already have this?"
CADDY_TAG="2.10-alpine"
POSTGRES_TAG="16-alpine"
REDIS_TAG="7-alpine"
INFRA_SLUG="caddy${CADDY_TAG%%-*}-pg${POSTGRES_TAG%%-*}-redis${REDIS_TAG%%-*}"
INFRA_ARCHIVE="$OUTDIR/artifactflow-infra-${INFRA_SLUG}.tar.gz"

# Analyst-tools bundle — pandas/numpy offline wheels for the analyst machine
# that runs scripts/observability_report.py. Independent of the backend
# deployment; the analyst host can be a different machine entirely.
#
# Why this is NOT for py-spy anymore: py-spy is baked into the backend image
# (see Dockerfile builder stage), invoked via `docker compose ... exec backend py-spy`.
# That collapses the previous "ship binary, install on host, hope cloud
# allows host ptrace_scope=0" path into a single container-scope cap_add.
# This tar is now genuinely just analyst-side offline pip install material.
#
# Bump procedure: update PANDAS_VERSION / NUMPY_VERSION, re-run with
# --with-analyst-tools. wheels.lock.txt inside the tar records the actually-
# resolved transitive set so ops can diff bundles across rebuilds.
PANDAS_VERSION="2.2.3"
NUMPY_VERSION="1.26.4"

# Python version for `pip download --python-version` — the wheels are
# interpreter-tagged (`cp311` etc.). Analyst host running observability_report
# must use the same major.minor. Project requires 3.11+ (see CLAUDE.md).
ANALYST_PYTHON="3.11"
# manylinux2014 covers CentOS 7+, Ubuntu 18.04+, Debian 10+ — the realistic
# intranet target set. If a deploy needs older glibc, switch to manylinux2010.
ANALYST_PLATFORM="manylinux2014_x86_64"

# Slug encodes the three pinned versions (NECESSARY for identity). Same-slug
# bundles built at different times CAN still differ in transitive deps —
# wheels.lock.txt diff is the SUFFICIENT equivalence check. See the README
# written into the bundle below.
ANALYST_SLUG="pandas${PANDAS_VERSION}-numpy${NUMPY_VERSION}-py${ANALYST_PYTHON}"
ANALYST_ARCHIVE="$OUTDIR/artifactflow-analyst-tools-${ANALYST_SLUG}.tar.gz"
SANDBOX_ARCHIVE="$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.tar.gz"
SANDBOX_VERIFY_ARCHIVE="$OUTDIR/artifactflow-sandbox-verify-${VERSION}.tar.gz"
SANDBOX_GVISOR_ARCHIVE=""
CHECKSUM_INPUTS=()
CHECKSUM_OUTPUTS=()

INFRA_DESC=$([[ $WITH_INFRA == 1 ]] && echo "included" || echo "skipped (--app-only)")
SANDBOX_DESC=$([[ $WITH_SANDBOX == 1 ]] && echo "included" || echo "skipped")
ANALYST_DESC=$([[ $WITH_ANALYST_TOOLS == 1 ]] && echo "included" || echo "skipped")
echo "=== ArtifactFlow Release: ${VERSION} (platform: ${PLATFORM}, infra: ${INFRA_DESC}, sandbox: ${SANDBOX_DESC}, analyst-tools: ${ANALYST_DESC}) ==="

mkdir -p "$OUTDIR"

if (( RESUME )); then
  echo "Resume enabled: completed phases with matching inputs/outputs will be skipped."
fi

phase_forced() {
  local phase="$1" forced
  [[ ${#FORCE_PHASES[@]} -gt 0 ]] || return 1
  for forced in "${FORCE_PHASES[@]}"; do
    [[ "$forced" == "$phase" || "$forced" == "all" ]] && return 0
  done
  return 1
}

file_sha() {
  shasum -a 256 "$1" | awk '{print $1}'
}

artifact_inputs_digest() {
  local file
  for file in "${CHECKSUM_INPUTS[@]}"; do
    printf '%s|%s\n' "$(basename "$file")" "$(file_sha "$file")"
  done | shasum -a 256 | awk '{print $1}'
}

repo_fingerprint() {
  (
    cd "$ROOT"
    git ls-files --cached --others --exclude-standard -z . \
      | LC_ALL=C sort -z \
      | xargs -0 shasum -a 256 2>/dev/null \
      | shasum -a 256 \
      | awk '{print $1}'
  )
}

phase_input() {
  local phase="$1"
  case "$phase" in
    app|sandbox-image|config|deploy)
      printf '%s|%s|%s|%s\n' "$phase" "$VERSION" "$PLATFORM" "$REPO_FINGERPRINT"
      ;;
    infra)
      printf '%s|%s|%s|%s|%s|%s\n' "$phase" "$PLATFORM" "$CADDY_TAG" "$POSTGRES_TAG" "$REDIS_TAG" "$INFRA_SLUG"
      ;;
    gvisor)
      printf '%s|download|%s|%s\n' "$phase" "${GVISOR_VERSION:-20260706.0}" "$GVISOR_ARCH"
      ;;
    analyst-tools)
      printf '%s|%s|%s|%s|%s\n' "$phase" "$PANDAS_VERSION" "$NUMPY_VERSION" "$ANALYST_PYTHON" "$ANALYST_PLATFORM"
      ;;
    checksums)
      printf '%s|%s\n' "$phase" "$(artifact_inputs_digest)"
      ;;
    manifest)
      printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$phase" "$VERSION" "$PLATFORM" "$WITH_INFRA" "$WITH_SANDBOX" "$WITH_ANALYST_TOOLS" "${SANDBOX_GVISOR_ARCHIVE:-}" "$(artifact_inputs_digest)"
      ;;
    *)
      printf '%s|%s|%s\n' "$phase" "$VERSION" "$PLATFORM"
      ;;
  esac
}

phase_done() {
  local phase="$1" input="$2"; shift 2
  (( RESUME )) || return 1
  phase_forced "$phase" && return 1
  local stamp="$STATE_DIR/$phase.stamp"
  [[ -f "$stamp" ]] || return 1
  grep -qx "phase=$phase" "$stamp" || return 1
  grep -qx "version=$VERSION" "$stamp" || return 1
  grep -qx "platform=$PLATFORM" "$stamp" || return 1
  grep -qx "input=$input" "$stamp" || return 1
  local out rel sha recorded
  for out in "$@"; do
    [[ -f "$out" ]] || return 1
    rel="${out#"$ROOT"/}"
    sha="$(file_sha "$out")"
    recorded="$(grep -F "output=$rel|" "$stamp" | tail -1 | cut -d'|' -f2- || true)"
    [[ "$recorded" == "$sha" ]] || return 1
  done
  echo "Skipping $phase (checkpoint valid)"
  return 0
}

write_phase_stamp() {
  local phase="$1" input="$2"; shift 2
  mkdir -p "$STATE_DIR"
  local stamp="$STATE_DIR/$phase.stamp"
  local tmp="$stamp.tmp"
  {
    printf 'phase=%s\n' "$phase"
    printf 'version=%s\n' "$VERSION"
    printf 'platform=%s\n' "$PLATFORM"
    printf 'input=%s\n' "$input"
    local out rel
    for out in "$@"; do
      rel="${out#"$ROOT"/}"
      printf 'output=%s|%s\n' "$rel" "$(file_sha "$out")"
    done
  } > "$tmp"
  mv -f "$tmp" "$stamp"
}

phase_outputs() {
  local phase="$1"
  local stamp="$STATE_DIR/$phase.stamp"
  local rel
  [[ -f "$stamp" ]] || return 1
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue
    if [[ "$rel" = /* ]]; then
      printf '%s\n' "$rel"
    else
      printf '%s/%s\n' "$ROOT" "$rel"
    fi
  done < <(awk -F'[=|]' '/^output=/{print $2}' "$stamp")
}

first_phase_output() {
  phase_outputs "$1" | head -1
}

REPO_FINGERPRINT="$(repo_fingerprint)"

APP_IMAGES=(
  "artifactflow:${VERSION}"
  "artifactflow-frontend:${VERSION}"
)

app_input="$(phase_input app)"
if ! phase_done app "$app_input" "$APP_ARCHIVE"; then
  # Build application images via buildx so we can cross-compile to amd64.
  # `--load` writes the result into the local docker daemon (vs `--push` to a registry).
  echo "Building backend image..."
  docker buildx build --platform "${PLATFORM}" \
    -t "artifactflow:${VERSION}" -t artifactflow:latest \
    --build-arg "ARTIFACTFLOW_SANDBOX_IMAGE=${SANDBOX_IMAGE_REF}" \
    --load .

  echo "Building frontend image..."
  docker buildx build --platform "${PLATFORM}" \
    -t "artifactflow-frontend:${VERSION}" -t artifactflow-frontend:latest \
    --build-arg NEXT_PUBLIC_API_URL= \
    --load ./frontend

  echo "Saving app images to ${APP_ARCHIVE}..."
  docker save "${APP_IMAGES[@]}" | gzip > "$APP_ARCHIVE"
  write_phase_stamp app "$app_input" "$APP_ARCHIVE"
fi

if [[ $WITH_INFRA == 1 ]]; then
  INFRA_IMAGES=(
    "caddy:${CADDY_TAG}"
    "postgres:${POSTGRES_TAG}"
    "redis:${REDIS_TAG}"
  )
  infra_input="$(phase_input infra)"
  if ! phase_done infra "$infra_input" "$INFRA_ARCHIVE"; then
    # Pull infra images for the target platform. We re-pull when the locally
    # cached image is for a different arch (common on Apple Silicon: previous
    # `docker pull` left an arm64 cache).
    for img in "${INFRA_IMAGES[@]}"; do
      current_arch=$(docker image inspect "$img" --format '{{.Architecture}}' 2>/dev/null || echo "missing")
      expected_arch="${ARCH_TAG}"
      if [[ "$current_arch" != "$expected_arch" ]]; then
        echo "Pulling $img for $PLATFORM (was: $current_arch)..."
        docker pull --platform "$PLATFORM" "$img"
      fi
    done
    echo "Saving infra images to ${INFRA_ARCHIVE}..."
    docker save "${INFRA_IMAGES[@]}" | gzip > "$INFRA_ARCHIVE"
    write_phase_stamp infra "$infra_input" "$INFRA_ARCHIVE"
  fi
fi

if [[ $WITH_SANDBOX == 1 ]]; then
  sandbox_input="$(phase_input sandbox-image)"
  if ! phase_done sandbox-image "$sandbox_input" "$SANDBOX_ARCHIVE" "$SANDBOX_VERIFY_ARCHIVE"; then
    echo "Building sandbox image + verify probes..."
    PLATFORM="$PLATFORM" "$ROOT/scripts/build-sandbox-image.sh" "$VERSION"
    [[ -f "$SANDBOX_ARCHIVE" ]] || { echo "Expected sandbox archive missing: $SANDBOX_ARCHIVE" >&2; exit 1; }
    [[ -f "$SANDBOX_VERIFY_ARCHIVE" ]] || { echo "Expected sandbox verify archive missing: $SANDBOX_VERIFY_ARCHIVE" >&2; exit 1; }
    write_phase_stamp sandbox-image "$sandbox_input" "$SANDBOX_ARCHIVE" "$SANDBOX_VERIFY_ARCHIVE"
  fi

  gvisor_input="$(phase_input gvisor)"
  previous_gvisor_output="$(first_phase_output gvisor || true)"
  if [[ -n "$previous_gvisor_output" ]] && phase_done gvisor "$gvisor_input" "$previous_gvisor_output"; then
    SANDBOX_GVISOR_ARCHIVE="$previous_gvisor_output"
  else
    echo "Packaging gVisor offline installer (${GVISOR_ARCH})..."
    ARCH="$GVISOR_ARCH" "$ROOT/sandbox/gvisor-pkg/fetch-and-package.sh"
    SANDBOX_GVISOR_ARCHIVE="$(find "$OUTDIR" -maxdepth 1 -type f -name "sandbox-gvisor-*-${GVISOR_ARCH}.tar.gz" -print | sort | tail -1)"
    [[ -n "$SANDBOX_GVISOR_ARCHIVE" && -f "$SANDBOX_GVISOR_ARCHIVE" ]] || {
      echo "Expected gVisor archive missing for arch ${GVISOR_ARCH}" >&2
      exit 1
    }
    write_phase_stamp gvisor "$gvisor_input" "$SANDBOX_GVISOR_ARCHIVE"
  fi
fi

# Package config/ separately so operators can ship prompt / model changes
# without re-transferring the (larger) image tar. The intranet compose
# bind-mounts ../config:/app/config:ro, so config/ must sit next to deploy/
# on the target host.
config_input="$(phase_input config)"
if ! phase_done config "$config_input" "$CONFIG_ARCHIVE"; then
  echo "Packaging config/ to ${CONFIG_ARCHIVE}..."
  # --no-xattrs / --no-fflags: this runs on a macOS build host where /usr/bin/tar
  # is bsdtar, which otherwise records macOS metadata as pax headers (SCHILY.fflags,
  # LIBARCHIVE.xattr.com.apple.*) that GNU tar on the Linux target warns about on
  # extract. --exclude='.DS_Store' drops the Finder turd that carries those xattrs
  # (and which has no business on the target anyway).
  # COPYFILE_DISABLE: --no-xattrs does NOT cover bsdtar's separate --mac-metadata
  # layer, which emits AppleDouble `._<name>` SIBLING ENTRIES for any file carrying
  # xattrs — those land as real binary files on the Linux target and crashed agent
  # loading on 2026-06-12 (`._lead_agent.md` is not utf-8). COPYFILE_DISABLE=1 is
  # the documented kill switch; --exclude='._*' is the belt to the suspenders.
  export COPYFILE_DISABLE=1
  tar --no-xattrs --no-fflags --exclude='.DS_Store' --exclude='._*' -czf "$CONFIG_ARCHIVE" config/
  write_phase_stamp config "$config_input" "$CONFIG_ARCHIVE"
fi

# Package deploy/ (compose file, Caddyfiles, scripts, maintenance assets).
#
# Use git's visible-file set instead of "tar deploy/". That ships tracked files
# plus untracked, non-ignored additions from the working tree, while keeping
# target-local files out by construction: deploy/.env, fleet.conf, .fleet-state,
# cert private material, maintenance flags, and per-host .env overrides are all
# ignored and must never be copied from the build machine into a release bundle.
deploy_input="$(phase_input deploy)"
if ! phase_done deploy "$deploy_input" "$DEPLOY_ARCHIVE"; then
  echo "Packaging deploy/ to ${DEPLOY_ARCHIVE}..."
  # --no-xattrs / --no-fflags: see config tar above — silence the GNU-tar
  # "unknown extended header keyword" warnings on the target by not emitting
  # macOS metadata.
  (
    cd "$ROOT"
    git ls-files --cached --others --exclude-standard -z deploy \
      | tar --no-xattrs --no-fflags --null -T - -czf "$DEPLOY_ARCHIVE"
  )
  write_phase_stamp deploy "$deploy_input" "$DEPLOY_ARCHIVE"
fi

# Analyst-tools bundle — pandas/numpy offline wheels for the analyst machine
# that runs scripts/observability_report.py. Everything is fetched on the
# BUILD host (this script's host) and packed into a self-contained tar; the
# target intranet host installs with `pip install --no-index --find-links wheels`
# and zero network calls.
#
# py-spy is NOT in this tar — it now lives inside the backend image
# (Dockerfile builder stage) + compose cap_add: [SYS_PTRACE]. See
# fix plan PR-forensics-bundle round 3 for why.
if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
  analyst_input="$(phase_input analyst-tools)"
  if ! phase_done analyst-tools "$analyst_input" "$ANALYST_ARCHIVE"; then
    STAGE="$OUTDIR/analyst-tools-stage"
    rm -rf "$STAGE"
    mkdir -p "$STAGE/wheels"

    # --platform / --python-version / --only-binary lock the download to wheels
    # that will install on a manylinux2014 x86_64 CPython 3.11 target. Without
    # these flags, pip happily downloads wheels matching the BUILD host (macOS
    # arm64) which then fail at `pip install` on the intranet target.
    #
    # Top-level versions are pinned (PANDAS_VERSION / NUMPY_VERSION); transitive
    # deps flow from pip's resolver. wheels.lock.txt records the actually-
    # resolved set (basenames, sorted) so ops can diff two bundles built at
    # different times — the slug encodes pinned versions only (NECESSARY),
    # wheels.lock is the SUFFICIENT check (catches transitive drift).
    echo "Downloading pandas==${PANDAS_VERSION} + numpy==${NUMPY_VERSION} wheels (target: ${ANALYST_PLATFORM}, py${ANALYST_PYTHON})..."
    if ! pip download \
        --platform "$ANALYST_PLATFORM" \
        --python-version "$ANALYST_PYTHON" \
        --only-binary=:all: \
        --dest "$STAGE/wheels" \
        "pandas==${PANDAS_VERSION}" "numpy==${NUMPY_VERSION}" >/dev/null; then
      cat >&2 <<EOF

ERROR: pip download failed. Possible causes:
  - PANDAS_VERSION (${PANDAS_VERSION}) / NUMPY_VERSION (${NUMPY_VERSION}) missing on PyPI
  - Build host has no network / pip < 23.0 / pip can't resolve --platform
EOF
      exit 1
    fi
    # Lock file: basenames, sorted, one per line. Filename-only — diff-friendly,
    # and PyPI's immutability policy makes hashing over-engineered.
    (cd "$STAGE/wheels" && ls *.whl | sort > ../wheels.lock.txt)
    wheel_total=$(wc -l < "$STAGE/wheels.lock.txt" | tr -d ' ')
    echo "  ✓ ${wheel_total} wheels resolved (recorded in wheels.lock.txt)"

    # README inside the analyst-tools tar — operator reads this without untarring
    # the whole bundle. Short on purpose; deployment SOP carries the full flow.
    cat > "$STAGE/README.md" <<EOF
ArtifactFlow analyst-tools bundle (${ANALYST_SLUG})

Built: $(date -u +%Y-%m-%dT%H:%M:%SZ)
pandas: ${PANDAS_VERSION}
numpy:  ${NUMPY_VERSION}
Python target: ${ANALYST_PYTHON} / ${ANALYST_PLATFORM}

Role:
  pandas/numpy — offline wheels for scripts/observability_report.py
                 (post-incident log/event analysis). Independent of the
                 backend deployment; analyst host can be a separate machine.

  NB: py-spy used to ship here too but now lives inside the backend image
  (Dockerfile + compose cap_add: [SYS_PTRACE]). For in-container forensics:
    docker compose -f deploy/docker-compose.intranet.yml exec backend \\
      py-spy dump --pid 1
  (service-name-based; compose-generated container name is not \`backend\`)

Contents:
  wheels/*.whl    — pandas + numpy + transitive deps for offline install
  wheels.lock.txt — sorted basenames of every wheel in wheels/. Use this
                    to verify two bundles are equivalent: the slug encodes
                    pinned top-level versions (NECESSARY), wheels.lock
                    catches transitive drift (SUFFICIENT). Diff:
                      diff bundleA/analyst-tools/wheels.lock.txt bundleB/...
  README.md       — this file

Install (analyst host, no network needed):
  pip install --no-index --find-links wheels pandas

Verify:
  python -c 'import pandas; print(pandas.__version__)'

See: docs/_archive/ops/deployment-sop.md → "取证就绪"
     docs/runbooks/service-hang.md (after PR-doc-runbook lands)
EOF

    echo "Packaging analyst-tools bundle to ${ANALYST_ARCHIVE}..."
    # Rename stage → analyst-tools so the tar lays out as analyst-tools/{wheels,README.md}
    # on the target host. (Avoid GNU `tar --transform` for macOS build-host compat.)
    rm -rf "$OUTDIR/analyst-tools"
    mv "$STAGE" "$OUTDIR/analyst-tools"
    tar -czf "$ANALYST_ARCHIVE" -C "$OUTDIR" analyst-tools
    rm -rf "$OUTDIR/analyst-tools"
    write_phase_stamp analyst-tools "$analyst_input" "$ANALYST_ARCHIVE"
  fi
fi

CHECKSUM_INPUTS=("$APP_ARCHIVE" "$CONFIG_ARCHIVE" "$DEPLOY_ARCHIVE")
[[ $WITH_INFRA == 1 ]] && CHECKSUM_INPUTS+=("$INFRA_ARCHIVE")
if [[ $WITH_SANDBOX == 1 ]]; then
  CHECKSUM_INPUTS+=("$SANDBOX_ARCHIVE" "$SANDBOX_VERIFY_ARCHIVE" "$SANDBOX_GVISOR_ARCHIVE")
fi
[[ $WITH_ANALYST_TOOLS == 1 ]] && CHECKSUM_INPUTS+=("$ANALYST_ARCHIVE")

CHECKSUM_OUTPUTS=()
for artifact in "${CHECKSUM_INPUTS[@]}"; do
  CHECKSUM_OUTPUTS+=("$artifact.sha256")
done

# Checksums — run from inside $OUTDIR so the .sha256 file records the bare
# filename instead of `dist/...`. Otherwise `sha256sum -c` fails on the
# target host where the tar was copied into a different directory.
checksums_input="$(phase_input checksums)"
if ! phase_done checksums "$checksums_input" "${CHECKSUM_OUTPUTS[@]}"; then
  (
    cd "$OUTDIR"
    for artifact in "${CHECKSUM_INPUTS[@]}"; do
      f="$(basename "$artifact")"
      sha256sum "$f" > "$f.sha256"
    done
  )
  write_phase_stamp checksums "$checksums_input" "${CHECKSUM_OUTPUTS[@]}"
fi

# Manifest — single text file capturing what's in this release. Ops can carry it
# alongside the tars to compare against the running deployment without
# untarring anything.
manifest_input="$(phase_input manifest)"
if ! phase_done manifest "$manifest_input" "$MANIFEST"; then
{
  echo "ArtifactFlow Release ${VERSION}"
  echo "Built:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "Built from:   $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')@$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "Platform:     ${PLATFORM}"
  echo "Sandbox image required: ${SANDBOX_IMAGE_REF}"
  LAYOUT_DESC="app + config + deploy"
  [[ $WITH_INFRA == 1 ]] && LAYOUT_DESC+=" + infra"
  [[ $WITH_SANDBOX == 1 ]] && LAYOUT_DESC+=" + sandbox"
  [[ $WITH_ANALYST_TOOLS == 1 ]] && LAYOUT_DESC+=" + analyst-tools"
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
    echo "  caddy:${CADDY_TAG}"
    echo "  postgres:${POSTGRES_TAG}"
    echo "  redis:${REDIS_TAG}"
  else
    echo "Infra images: skipped — target must already have these loaded:"
    echo "  caddy:${CADDY_TAG}"
    echo "  postgres:${POSTGRES_TAG}"
    echo "  redis:${REDIS_TAG}"
    echo "  (run release with --with-infra to ship them)"
  fi
  echo ""
  if [[ $WITH_SANDBOX == 1 ]]; then
    echo "Sandbox bundle:"
    echo "  required: ${SANDBOX_IMAGE_REF}"
    echo "  image:   $(basename "$SANDBOX_ARCHIVE")"
    echo "  verify:  $(basename "$SANDBOX_VERIFY_ARCHIVE")"
    echo "  gVisor:  $(basename "$SANDBOX_GVISOR_ARCHIVE")"
    echo "  target:  deploy/scripts/fleet.sh prepare-sandbox <bundle-dir>, then"
    echo "           AF_ENABLE_SANDBOX=1 deploy/scripts/fleet.sh deploy <bundle-dir>"
    if [[ -f "$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.manifest.txt" ]]; then
      image_id=$(awk '/^Image id:/ {sub(/^Image id:[[:space:]]*/, ""); print; exit}' "$OUTDIR/artifactflow-sandbox-${VERSION}-${ARCH_TAG}.manifest.txt")
      [[ -n "$image_id" ]] && echo "  image id: $image_id"
    fi
  else
    echo "Sandbox bundle: skipped — target must already have runsc +"
    echo "  ${SANDBOX_IMAGE_REF} + scratch root, or re-run release with"
    echo "  --with-sandbox to ship the required immutable sandbox image."
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
    | grep -E '^deploy/[^/]+/$|\.sh$|\.yml$|Caddyfile|\.caddy$|\.env\.example$' \
    | sort -u \
    | sed 's/^/  /'
  echo ""
  if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
    echo "Analyst-tools bundle (artifactflow-analyst-tools-${ANALYST_SLUG}.tar.gz):"
    echo "  pandas:        ${PANDAS_VERSION}"
    echo "  numpy:         ${NUMPY_VERSION}"
    echo "  Python target: ${ANALYST_PYTHON} / ${ANALYST_PLATFORM}"
    wheel_count=$(tar tzf "$ANALYST_ARCHIVE" | grep -c '\.whl$' || true)
    echo "  Wheels:        ${wheel_count} files (pandas + numpy + transitive,"
    echo "                 full list in analyst-tools/wheels.lock.txt)"
  else
    echo "Analyst-tools bundle: skipped — analyst host must already have"
    echo "  pandas/numpy installed (run release with --with-analyst-tools"
    echo "  to ship offline wheels; see docs/_archive/ops/deployment-sop.md)."
  fi
  echo ""
  echo "Backend image embeds py-spy (Dockerfile builder stage); compose enables"
  echo "cap_add: [SYS_PTRACE] for the backup attach path:"
  echo "  docker compose -f deploy/docker-compose.intranet.yml exec backend \\"
  echo "    py-spy dump --pid 1"
} > "$MANIFEST"
  write_phase_stamp manifest "$manifest_input" "$MANIFEST"
fi

echo ""
echo "=== Release artifacts ==="
ls -lh "$OUTDIR"/artifactflow-{app,config,deploy}-"${VERSION}".tar.gz{,.sha256} "$MANIFEST" 2>/dev/null
if [[ $WITH_INFRA == 1 ]]; then
  ls -lh "$INFRA_ARCHIVE" "$INFRA_ARCHIVE.sha256"
fi
if [[ $WITH_SANDBOX == 1 ]]; then
  ls -lh "$SANDBOX_ARCHIVE" "$SANDBOX_ARCHIVE.sha256" \
         "$SANDBOX_VERIFY_ARCHIVE" "$SANDBOX_VERIFY_ARCHIVE.sha256" \
         "$SANDBOX_GVISOR_ARCHIVE" "$SANDBOX_GVISOR_ARCHIVE.sha256"
fi
if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
  ls -lh "$ANALYST_ARCHIVE" "$ANALYST_ARCHIVE.sha256"
fi
echo ""
echo "Manifest preview (first 30 lines):"
head -30 "$MANIFEST" | sed 's/^/  /'
echo ""

# Recipe is rendered conditionally on the flags actually used this build. It
# lists the files that must be present in the target bundle directory, but leaves
# the physical transfer mechanism to the deployment site's approved medium.
if [[ $WITH_INFRA == 1 ]]; then
  INFRA_ARTIFACTS=$'\n  #   artifactflow-infra-'"${INFRA_SLUG}"$'.tar.gz{,.sha256}'
  INFRA_FOOTER=""
else
  INFRA_ARTIFACTS=""
  INFRA_FOOTER="  # (infra tar omitted — re-run release with --with-infra to ship caddy/postgres/redis images)"
fi
if [[ $WITH_SANDBOX == 1 ]]; then
  SANDBOX_ARTIFACTS=$'\n  #   artifactflow-sandbox-'"${VERSION}-${ARCH_TAG}"$'.tar.gz{,.sha256}\n  #   artifactflow-sandbox-verify-'"${VERSION}"$'.tar.gz{,.sha256}\n  #   '"$(basename "$SANDBOX_GVISOR_ARCHIVE")"$'{,.sha256}'
  SANDBOX_PREP_LOCAL=$'\n    # Requires root: installs/registers runsc and mounts the sandbox scratch loop.\n    sudo env AF_BUNDLE_VERSION='"${VERSION}"$' \\\n      AF_SANDBOX_POOL=/data/artifactflow/sandbox-pool.img \\\n      AF_SANDBOX_SCRATCH_ROOT=/data/artifactflow/sandbox-scratch \\\n      AF_SANDBOX_POOL_SIZE=80G \\\n      deploy/scripts/fleet.sh prepare-sandbox "$BUNDLE"'
  SANDBOX_PREP_TMP=$'\n    # Requires root: refreshes runsc/sandbox image/scratch prerequisites from this bundle.\n    sudo env AF_BUNDLE_VERSION='"${VERSION}"$' \\\n      AF_SANDBOX_POOL=/data/artifactflow/sandbox-pool.img \\\n      AF_SANDBOX_SCRATCH_ROOT=/data/artifactflow/sandbox-scratch \\\n      AF_SANDBOX_POOL_SIZE=80G \\\n      ./deploy/scripts/fleet.sh prepare-sandbox "$BUNDLE"'
  SANDBOX_UP_PREFIX="AF_ENABLE_SANDBOX=1 "
  SANDBOX_FOOTER=""
else
  SANDBOX_ARTIFACTS=""
  SANDBOX_PREP_LOCAL=""
  SANDBOX_PREP_TMP=""
  SANDBOX_UP_PREFIX=""
  SANDBOX_FOOTER="  # (sandbox bundle omitted — re-run release with --with-sandbox to ship runsc + sandbox image + verify probes)"
fi
if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
  ANALYST_ARTIFACTS=$'\n  #   artifactflow-analyst-tools-'"${ANALYST_SLUG}"$'.tar.gz{,.sha256}'
  ANALYST_RECIPE=$'\n    tar xzf "$BUNDLE/artifactflow-analyst-tools-'"${ANALYST_SLUG}"$'.tar.gz"   # → ./analyst-tools/\n    # Offline wheels: install on the machine running observability_report.py.\n    pip install --no-index --find-links analyst-tools/wheels pandas'
  ANALYST_FOOTER=""
else
  ANALYST_ARTIFACTS=""
  ANALYST_RECIPE=""
  ANALYST_FOOTER="  # (analyst-tools tar omitted — re-run release with --with-analyst-tools to ship offline pandas/numpy wheels)"
fi

cat <<EOF
To deploy on air-gapped host:

  # Transfer artifacts with your site's approved medium. The target bundle
  # directory must contain the listed files before running the target-host steps.

  # ---- First-time deployment ----
$INFRA_FOOTER
$SANDBOX_FOOTER
$ANALYST_FOOTER
  # Target bundle directory: /root/workspace/tmp/${VERSION}/
  # Required files for this build:
  #   artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256}${INFRA_ARTIFACTS}${SANDBOX_ARTIFACTS}${ANALYST_ARTIFACTS}
  #   artifactflow-${VERSION}.manifest.txt

  # On the target host:
    VERSION=${VERSION}
    BUNDLE=/root/workspace/tmp/\$VERSION
    APP=/root/workspace/artifactflow
    mkdir -p "\$BUNDLE" "\$APP"
    cd "\$APP"
    tar xzf "\$BUNDLE/artifactflow-deploy-${VERSION}.tar.gz"
    deploy/scripts/verify-bundle.sh "\$BUNDLE"
    deploy/scripts/fleet.sh init-local --scale 2
    vi deploy/.env
    vi deploy/fleet.conf
${SANDBOX_PREP_LOCAL}
    ${SANDBOX_UP_PREFIX}AF_BUNDLE_VERSION=${VERSION} deploy/scripts/fleet.sh deploy "\$BUNDLE"
    ${ANALYST_RECIPE}
    # No pause/resume here — there's nothing running to pause.
    # Preflight pass 2 — after \`up\`, now verifies py-spy lives in the image.
    ./deploy/scripts/preflight.sh

  # ---- Roll-update (no infra, no analyst-tools re-ship) ----
  # Target bundle directory: /root/workspace/tmp/${VERSION}/
  # Required files for this build:
  #   artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256}${SANDBOX_ARTIFACTS}
  #   artifactflow-${VERSION}.manifest.txt

  # On the target host:
    VERSION=${VERSION}
    BUNDLE=/root/workspace/tmp/\$VERSION
    APP=/root/workspace/artifactflow
    cd "\$APP"
    ./deploy/scripts/verify-bundle.sh "\$BUNDLE"
    # Self-bootstrap deploy scripts before invoking fleet: older fleet.sh
    # versions do not know how to extract deploy/config from the bundle.
    tar xzf "\$BUNDLE/artifactflow-deploy-${VERSION}.tar.gz"
${SANDBOX_PREP_TMP}
    # ─── compose infra changes (rare) ────────────────────────────
    # If this version changed compose \`caddy\` / \`postgres\` / \`redis\` service
    # blocks (image / logging / mem_limit / volumes / ports / command), the
    # Caddyfiles, or .env's AF_HTTP_PORT / AF_HTTPS_PORT (caddy ports:
    # interpolation), resume.sh won't propagate the change — see
    # docs/deployment.md → 滚动更新已有部署 → "涉及 compose infra 服务 config
    # 变更的升级". Recreate windows:
    #   - caddy / AF_HTTP(S)_PORT: recreate BEFORE pause.sh below (keeps the
    #     maintenance page servable through the window; Caddy dials upstreams
    #     lazily, so recreating with backends stopped is also safe)
    #   - PG/Redis: recreate between pause and resume
    # POSTGRES_* env are init-only — changing user/password/db on a live
    # cluster needs SQL (\`ALTER USER ...\`), NOT container recreate.
    # Most releases skip this entire block.
    # ─────────────────────────────────────────────────────────────
    # Fleet deploy is a direct compose up. For a maintenance-page window instead,
    # run maintenance.sh on before deploy and maintenance.sh off after it succeeds.
    ${SANDBOX_UP_PREFIX}AF_BUNDLE_VERSION=${VERSION} ./deploy/scripts/fleet.sh deploy "\$BUNDLE"
EOF
