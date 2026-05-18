#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
#
# Usage:
#   ./scripts/release.sh [VERSION] [--with-infra | --app-only] [--with-analyst-tools]
#
# Defaults:
#   VERSION:     $(date +%Y%m%d)
#   layout:      --app-only (skip infra images, skip analyst tools)
#
# Output (in dist/):
#   artifactflow-app-<VERSION>.tar.gz             backend + frontend images
#   artifactflow-config-<VERSION>.tar.gz          config/ tree (prompts + site + models)
#   artifactflow-deploy-<VERSION>.tar.gz          deploy/ tree (compose + nginx + scripts)
#   artifactflow-<VERSION>.manifest.txt           human-readable release manifest
#   *.sha256                                       per-tar checksums
#   artifactflow-infra-<infra-slug>.tar.gz        ONLY if --with-infra (content-
#                                                  addressed by base image tags).
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
  sed -n '1,38p' "$0"
}

VERSION=""
WITH_INFRA=0
WITH_ANALYST_TOOLS=0
for arg in "$@"; do
  case "$arg" in
    --with-infra)         WITH_INFRA=1 ;;
    --app-only)           WITH_INFRA=0 ;;
    --with-analyst-tools) WITH_ANALYST_TOOLS=1 ;;
    -h|--help)            show_help; exit 0 ;;
    -*)                   echo "Unknown flag: $arg (use -h for usage)" >&2; exit 2 ;;
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

# Analyst-tools bundle — pandas/numpy offline wheels for the analyst machine
# that runs scripts/observability_report.py. Independent of the backend
# deployment; the analyst host can be a different machine entirely.
#
# Why this is NOT for py-spy anymore: py-spy is baked into the backend image
# (see Dockerfile builder stage), invoked via `docker exec backend py-spy`.
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

# Build platform — default linux/amd64 because the intranet target is x86_64.
# Apple Silicon Macs default to linux/arm64 without --platform, producing
# images that fail at startup on the server with "exec format error".
# See docs/_archive/intranet部署运维笔记.md → "macOS arm64 → Linux amd64".
PLATFORM="${PLATFORM:-linux/amd64}"

INFRA_DESC=$([[ $WITH_INFRA == 1 ]] && echo "included" || echo "skipped (--app-only)")
ANALYST_DESC=$([[ $WITH_ANALYST_TOOLS == 1 ]] && echo "included" || echo "skipped")
echo "=== ArtifactFlow Release: ${VERSION} (platform: ${PLATFORM}, infra: ${INFRA_DESC}, analyst-tools: ${ANALYST_DESC}) ==="

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
  (Dockerfile + compose cap_add: [SYS_PTRACE]). For in-container forensics
  use \`docker exec backend py-spy ...\` directly.

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
fi

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
  if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
    f=$(basename "$ANALYST_ARCHIVE")
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
  echo "cap_add: [SYS_PTRACE] for \`docker exec backend py-spy ...\` backup path."
} > "$MANIFEST"

echo ""
echo "=== Release artifacts ==="
ls -lh "$OUTDIR"/artifactflow-{app,config,deploy}-"${VERSION}".tar.gz{,.sha256} "$MANIFEST" 2>/dev/null
if [[ $WITH_INFRA == 1 ]]; then
  ls -lh "$INFRA_ARCHIVE" "$INFRA_ARCHIVE.sha256"
fi
if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
  ls -lh "$ANALYST_ARCHIVE" "$ANALYST_ARCHIVE.sha256"
fi
echo ""
echo "Manifest preview (first 30 lines):"
head -30 "$MANIFEST" | sed 's/^/  /'
echo ""

# Recipe is rendered conditionally on the flags actually used this build, so
# copy-paste-able lines match what was produced (no "scp a tar you didn't
# build"). Inline lines carry a leading newline + indent + trailing `\` so the
# enclosing scp/etc. continuation stays unbroken when the chunk is empty.
if [[ $WITH_INFRA == 1 ]]; then
  INFRA_SCP_LN=$'\n      dist/artifactflow-infra-'"${INFRA_SLUG}"$'.tar.gz{,.sha256}                     \\'
  INFRA_LOAD_LN=$'\n    docker load -i artifactflow-infra-'"${INFRA_SLUG}"$'.tar.gz'
  INFRA_FOOTER=""
else
  INFRA_SCP_LN=""
  INFRA_LOAD_LN=""
  INFRA_FOOTER="  # (infra tar omitted — re-run release with --with-infra to ship nginx/postgres/redis images)"
fi
if [[ $WITH_ANALYST_TOOLS == 1 ]]; then
  ANALYST_SCP_LN=$'\n      dist/artifactflow-analyst-tools-'"${ANALYST_SLUG}"$'.tar.gz{,.sha256}           \\'
  ANALYST_RECIPE=$'\n    tar xzf artifactflow-analyst-tools-'"${ANALYST_SLUG}"$'.tar.gz   # → ./analyst-tools/\n    # Offline wheels: install on the machine running observability_report.py.\n    pip install --no-index --find-links analyst-tools/wheels pandas'
  ANALYST_FOOTER=""
else
  ANALYST_SCP_LN=""
  ANALYST_RECIPE=""
  ANALYST_FOOTER="  # (analyst-tools tar omitted — re-run release with --with-analyst-tools to ship offline pandas/numpy wheels)"
fi

cat <<EOF
To deploy on air-gapped host:

  # ---- First-time deployment ----
$INFRA_FOOTER
$ANALYST_FOOTER
  scp dist/artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256}         \\${INFRA_SCP_LN}${ANALYST_SCP_LN}
      dist/artifactflow-${VERSION}.manifest.txt                                   \\
      target:/opt/artifactflow/
  ssh target
    cd /opt/artifactflow
    # verify-bundle.sh lives inside deploy/, which isn't extracted yet — use
    # plain sha256sum. Glob is safe in a fresh dir, and CWD matches where
    # each .sha256 records its filename.
    sha256sum -c artifactflow-*.tar.gz.sha256
    tar xzf artifactflow-deploy-${VERSION}.tar.gz
    tar xzf artifactflow-config-${VERSION}.tar.gz${INFRA_LOAD_LN}
    docker load -i artifactflow-app-${VERSION}.tar.gz${ANALYST_RECIPE}
    # Preflight: 2 checks (analyst-tools wheels resolve + py-spy in backend container).
    ./deploy/scripts/preflight.sh
    cp deploy/.env.intranet.example deploy/.env && vi deploy/.env
    AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d
    # No pause/resume here — there's nothing running to pause.

  # ---- Roll-update (no infra, no analyst-tools re-ship) ----
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
