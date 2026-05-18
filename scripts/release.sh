#!/bin/bash
set -euo pipefail

# Build, tag, and package ArtifactFlow images for air-gapped deployment.
#
# Usage:
#   ./scripts/release.sh [VERSION] [--with-infra | --app-only] [--with-forensics]
#
# Defaults:
#   VERSION:     $(date +%Y%m%d)
#   layout:      --app-only (skip infra images, skip forensics)
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
#   artifactflow-forensics-<forensics-slug>.tar.gz  ONLY if --with-forensics (content-
#                                              addressed by py-spy + python versions;
#                                              py-spy static binary + pandas/numpy
#                                              offline wheels for `observability_report.py`).
#
# Air-gap contract:
#   Everything downloaded by this script is downloaded on the BUILD host.
#   Target intranet hosts MUST be able to deploy with zero network calls
#   (no `pip install <pkgname>` against PyPI, no `curl github`, etc.). All
#   transitive dependencies of pandas/numpy are pre-downloaded into the
#   forensics tar so `pip install --no-index --find-links wheels pandas`
#   resolves offline.

show_help() {
  sed -n '1,32p' "$0"
}

VERSION=""
WITH_INFRA=0
WITH_FORENSICS=0
for arg in "$@"; do
  case "$arg" in
    --with-infra)     WITH_INFRA=1 ;;
    --app-only)       WITH_INFRA=0 ;;
    --with-forensics) WITH_FORENSICS=1 ;;
    -h|--help)        show_help; exit 0 ;;
    -*)               echo "Unknown flag: $arg (use -h for usage)" >&2; exit 2 ;;
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

# Forensics bundle — pinned versions so the tar is reproducible and target
# ops can be told exactly what they're getting. Two layers of verification:
#   1. PYSPY_SHA256 pins the **extracted binary** SHA. Mismatch hard-fails the
#      build (catches PyPI tamper / wheel layout change / wrong wheel pulled).
#   2. forensics/bin/py-spy.sha256 in the bundle lets preflight on the target
#      host re-verify after extraction (same `sha256sum -c` check, same file
#      contents, zero drift surface).
# Bump procedure:
#   1. Update PYSPY_VERSION (and/or PANDAS_VERSION / NUMPY_VERSION).
#   2. Run release.sh --with-forensics. SHA mismatch (or empty SHA) will print
#      the actual SHA — paste it into PYSPY_SHA256 and re-run.
PYSPY_VERSION="0.4.1"
# py-spy is distributed as a wheel that bundles the static binary at
# `py_spy-<ver>.data/scripts/py-spy` (Python "data files in wheels" spec).
# We pull the wheel via `pip download` (uniform mechanism with pandas/numpy
# below) and extract the binary; the wheel itself is discarded. The manylinux
# wheel is glibc-linked and works on CentOS 7+ / Ubuntu 18.04+ / Debian 10+,
# which covers the realistic intranet target set. Alpine (musl) would need
# the musllinux wheel — add a second branch if a deploy ever requires it.
#
# SHA256 of the EXTRACTED py-spy binary (not the wheel — the binary is the
# artifact we care about). To populate on first build / version bump: leave
# empty, run release.sh --with-forensics, copy the printed hash here.
PYSPY_SHA256="${PYSPY_SHA256:-}"

# pandas / numpy top-level pins. `pip download` resolves these along with all
# transitive deps; the resolved set is recorded into forensics/wheels.lock.txt
# inside the bundle so ops can diff "what did I get last time vs. this time"
# even though the slug only captures top-level versions.
PANDAS_VERSION="2.2.3"
NUMPY_VERSION="1.26.4"

# Python version for `pip download --python-version` — the wheels are
# interpreter-tagged (`cp311` etc.). Analyst host running observability_report
# must use the same major.minor. Project requires 3.11+ (see CLAUDE.md).
FORENSICS_PYTHON="3.11"
# manylinux2014 covers CentOS 7+, Ubuntu 18.04+, Debian 10+ — the realistic
# intranet target set. If a deploy needs older glibc, switch to manylinux2010.
FORENSICS_PLATFORM="manylinux2014_x86_64"

# Content-addressed tar name — same idea as INFRA_SLUG: ops can see at a
# glance "do I already have this exact forensics bundle?". py-spy version
# + Python version are the dimensions that drive ABI compat; pandas/numpy
# pins do change content but are captured in the manifest + wheels.lock.txt,
# not the slug (keeping it readable).
FORENSICS_SLUG="pyspy${PYSPY_VERSION}-py${FORENSICS_PYTHON}"
FORENSICS_ARCHIVE="$OUTDIR/artifactflow-forensics-${FORENSICS_SLUG}.tar.gz"

# Build platform — default linux/amd64 because the intranet target is x86_64.
# Apple Silicon Macs default to linux/arm64 without --platform, producing
# images that fail at startup on the server with "exec format error".
# See docs/_archive/intranet部署运维笔记.md → "macOS arm64 → Linux amd64".
PLATFORM="${PLATFORM:-linux/amd64}"

INFRA_DESC=$([[ $WITH_INFRA == 1 ]] && echo "included" || echo "skipped (--app-only)")
FORENSICS_DESC=$([[ $WITH_FORENSICS == 1 ]] && echo "included" || echo "skipped")
echo "=== ArtifactFlow Release: ${VERSION} (platform: ${PLATFORM}, infra: ${INFRA_DESC}, forensics: ${FORENSICS_DESC}) ==="

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

# Forensics bundle — py-spy static binary + pandas/numpy offline wheels.
# Everything is fetched on the build host (this script's host) and packed
# into a self-contained tar; the target intranet host installs with
# `pip install --no-index --find-links wheels` and zero network calls.
#
# Reason it's a separate tar (not added to deploy/):
#   - Forensics tar is content-addressed by py-spy + Python version; if those
#     don't change, ops can skip re-shipping it across releases (same idea
#     as INFRA_ARCHIVE).
#   - py-spy + wheels are ~60MB; deploy tar should stay small (~30KB) so
#     code-only updates roll fast.
if [[ $WITH_FORENSICS == 1 ]]; then
  STAGE="$OUTDIR/forensics-stage"
  rm -rf "$STAGE"
  mkdir -p "$STAGE/bin" "$STAGE/wheels"

  # ---- py-spy ----
  # Pull the py-spy wheel via the same `pip download` mechanism as pandas/numpy,
  # then extract the bundled binary. The wheel is discarded — analyst host
  # doesn't need py-spy as a Python package, just the binary on PATH.
  PYSPY_DL="$OUTDIR/forensics-pyspy-dl"
  rm -rf "$PYSPY_DL"
  mkdir -p "$PYSPY_DL"
  echo "Downloading py-spy ${PYSPY_VERSION} wheel (target: ${FORENSICS_PLATFORM}, py${FORENSICS_PYTHON})..."
  # --no-deps: py-spy has no Python deps, and even if it did we'd be packing
  # only the binary, not running py-spy as a Python module.
  if ! pip download \
      --platform "$FORENSICS_PLATFORM" \
      --python-version "$FORENSICS_PYTHON" \
      --only-binary=:all: \
      --no-deps \
      --dest "$PYSPY_DL" \
      "py-spy==${PYSPY_VERSION}" >/dev/null; then
    cat >&2 <<EOF

ERROR: py-spy wheel download failed. Possible causes:
  - PYSPY_VERSION (${PYSPY_VERSION}) doesn't exist on PyPI
  - Build host has no network / pip < 23.0 / pip can't resolve --platform
EOF
    rm -rf "$PYSPY_DL"
    exit 1
  fi
  WHEEL=$(ls "$PYSPY_DL"/py_spy-*.whl 2>/dev/null | head -1)
  if [[ -z "$WHEEL" ]]; then
    echo "ERROR: pip download succeeded but no py_spy-*.whl found in $PYSPY_DL" >&2
    rm -rf "$PYSPY_DL"
    exit 1
  fi

  # Locate the binary inside the wheel. Path is `py_spy-<ver>.data/scripts/py-spy`
  # per the Python wheel "data files" spec, but we glob rather than hardcode
  # so the script keeps working if upstream renames .data/scripts/ → .data/bin/.
  BIN_PATH=$(unzip -l "$WHEEL" 2>/dev/null | awk '$NF ~ /\.data\/(scripts|bin)\/py-spy$/ {print $NF; exit}')
  if [[ -z "$BIN_PATH" ]]; then
    cat >&2 <<EOF

ERROR: couldn't find py-spy binary inside wheel. Upstream wheel layout
may have changed; inspect:
  unzip -l "$WHEEL"
EOF
    rm -rf "$PYSPY_DL"
    exit 1
  fi
  # -j flattens (drops the .data/scripts/ prefix), -o overwrites,
  # -d sets destination.
  unzip -j -o "$WHEEL" "$BIN_PATH" -d "$STAGE/bin" >/dev/null
  rm -rf "$PYSPY_DL"
  chmod +x "$STAGE/bin/py-spy"

  # SHA pin against the EXTRACTED binary (the artifact we ship). Mismatch
  # signals: upstream rebuild / wheel tamper / wrong-platform wheel pulled.
  actual_sha=$(sha256sum "$STAGE/bin/py-spy" | awk '{print $1}')
  if [[ -z "$PYSPY_SHA256" ]]; then
    # First-time / version-bump path. Print the SHA + abort so operator
    # explicitly pins it — an unverified binary in the forensics tar would
    # silently undermine the whole point of forensics.
    cat >&2 <<EOF

ERROR: PYSPY_SHA256 is empty. Verify py-spy ${PYSPY_VERSION} authenticity
(check PyPI / upstream changelog), then pin the EXTRACTED-BINARY SHA in
scripts/release.sh:

  PYSPY_SHA256="${actual_sha}"

Then re-run this script.
EOF
    exit 1
  fi
  if [[ "$actual_sha" != "$PYSPY_SHA256" ]]; then
    cat >&2 <<EOF

ERROR: py-spy binary SHA mismatch (possible upstream tamper, wheel-layout
change, or wrong-platform wheel pulled).
  expected: ${PYSPY_SHA256}
  actual:   ${actual_sha}
EOF
    exit 1
  fi
  echo "  ✓ py-spy binary SHA verified"

  # Write a sha256sum(1)-compatible file alongside the binary so preflight
  # on the target host can run `sha256sum -c py-spy.sha256` and re-verify
  # the same artifact. Two-space separator is sha256sum's wire format —
  # don't reformat.
  (cd "$STAGE/bin" && sha256sum py-spy > py-spy.sha256)

  # ---- pandas / numpy wheels (offline) ----
  # --platform / --python-version / --only-binary lock the download to wheels
  # that will install on a manylinux2014 x86_64 CPython 3.11 target. Without
  # these flags, pip happily downloads wheels matching the BUILD host (macOS
  # arm64) which then fail at `pip install` on the intranet target.
  #
  # Top-level versions are pinned (PANDAS_VERSION / NUMPY_VERSION); transitive
  # deps flow from pip's resolver. wheels.lock.txt records the actual resolved
  # set (basenames, sorted) so ops can diff two bundles built at different
  # times and tell whether they really are equivalent — the slug captures
  # py-spy + python versions only, by design (slug stays human-readable).
  echo "Downloading pandas==${PANDAS_VERSION} + numpy==${NUMPY_VERSION} wheels (target: ${FORENSICS_PLATFORM}, py${FORENSICS_PYTHON})..."
  if ! pip download \
      --platform "$FORENSICS_PLATFORM" \
      --python-version "$FORENSICS_PYTHON" \
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
  # Lock file: basenames, sorted, one per line. Filename-only (per project
  # decision) — diff-friendly and PyPI's immutability policy makes hashing
  # over-engineered.
  (cd "$STAGE/wheels" && ls *.whl | sort > ../wheels.lock.txt)
  wheel_total=$(wc -l < "$STAGE/wheels.lock.txt" | tr -d ' ')
  echo "  ✓ ${wheel_total} wheels resolved (recorded in wheels.lock.txt)"

  # README inside the forensics tar — target operators read this without
  # untarring the whole bundle (after extraction, it's adjacent to bin/ and
  # wheels/). Keep it short; the deployment SOP carries the full procedure.
  cat > "$STAGE/README.md" <<EOF
ArtifactFlow forensics bundle (${FORENSICS_SLUG})

Built: $(date -u +%Y-%m-%dT%H:%M:%SZ)
py-spy: ${PYSPY_VERSION}  (binary sha256: ${PYSPY_SHA256})
pandas: ${PANDAS_VERSION}
numpy:  ${NUMPY_VERSION}
Python target: ${FORENSICS_PYTHON} / ${FORENSICS_PLATFORM}

Contents:
  bin/py-spy           — static binary, drop into /usr/local/bin on intranet host
  bin/py-spy.sha256    — sha256sum(1)-compatible checksum file, used by preflight
  wheels/*.whl         — pandas + numpy + transitive deps for offline install
  wheels.lock.txt      — sorted basenames of every wheel in wheels/ (diff against
                         another bundle to tell if they really are equivalent;
                         the slug itself only encodes py-spy + python versions)
  README.md            — this file

Install (intranet host, no network needed):
  sudo install -m 0755 bin/py-spy /usr/local/bin/py-spy
  pip install --no-index --find-links wheels pandas

Verify:
  (cd bin && sha256sum -c py-spy.sha256)
  py-spy --version
  python -c 'import pandas; print(pandas.__version__)'

See: docs/_archive/ops/deployment-sop.md → "Forensics readiness"
     docs/runbooks/service-hang.md (after PR-doc-runbook lands)
EOF

  echo "Packaging forensics bundle to ${FORENSICS_ARCHIVE}..."
  # Rename stage → forensics so the tar lays out as forensics/{bin,wheels,README.md}
  # on the target host. (Avoid GNU `tar --transform` for macOS build-host compat.)
  rm -rf "$OUTDIR/forensics"
  mv "$STAGE" "$OUTDIR/forensics"
  tar -czf "$FORENSICS_ARCHIVE" -C "$OUTDIR" forensics
  rm -rf "$OUTDIR/forensics"
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
  if [[ $WITH_FORENSICS == 1 ]]; then
    f=$(basename "$FORENSICS_ARCHIVE")
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
  [[ $WITH_FORENSICS == 1 ]] && LAYOUT_DESC+=" + forensics"
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
  if [[ $WITH_FORENSICS == 1 ]]; then
    echo "Forensics bundle (artifactflow-forensics-${FORENSICS_SLUG}.tar.gz):"
    echo "  py-spy:        ${PYSPY_VERSION}  (binary sha256: ${PYSPY_SHA256:0:16}...)"
    echo "  pandas:        ${PANDAS_VERSION}"
    echo "  numpy:         ${NUMPY_VERSION}"
    echo "  Python target: ${FORENSICS_PYTHON} / ${FORENSICS_PLATFORM}"
    wheel_count=$(tar tzf "$FORENSICS_ARCHIVE" | grep -c '\.whl$' || true)
    echo "  Wheels:        ${wheel_count} files (pandas + numpy + transitive,"
    echo "                 full list in forensics/wheels.lock.txt)"
  else
    echo "Forensics bundle: skipped — target must already have py-spy + wheels"
    echo "  available (run release with --with-forensics to ship them; see"
    echo "  docs/_archive/ops/deployment-sop.md → 'Forensics readiness')"
  fi
} > "$MANIFEST"

echo ""
echo "=== Release artifacts ==="
ls -lh "$OUTDIR"/artifactflow-{app,config,deploy}-"${VERSION}".tar.gz{,.sha256} "$MANIFEST" 2>/dev/null
if [[ $WITH_INFRA == 1 ]]; then
  ls -lh "$INFRA_ARCHIVE" "$INFRA_ARCHIVE.sha256"
fi
if [[ $WITH_FORENSICS == 1 ]]; then
  ls -lh "$FORENSICS_ARCHIVE" "$FORENSICS_ARCHIVE.sha256"
fi
echo ""
echo "Manifest preview (first 30 lines):"
head -30 "$MANIFEST" | sed 's/^/  /'
echo ""
cat <<EOF
To deploy on air-gapped host:

  # ---- First-time deployment ----
  # Build must include --with-infra so the infra tar exists.
  # Also include --with-forensics on first deploy to install py-spy + analyst
  # wheels (zero-network on target).
  scp dist/artifactflow-{app,config,deploy}-${VERSION}.tar.gz{,.sha256}      \\
      dist/artifactflow-infra-${INFRA_SLUG}.tar.gz{,.sha256}                  \\
      dist/artifactflow-forensics-${FORENSICS_SLUG}.tar.gz{,.sha256}          \\
      dist/artifactflow-${VERSION}.manifest.txt                                \\
      target:/opt/artifactflow/
  ssh target
    cd /opt/artifactflow
    # verify-bundle.sh lives inside deploy/, which isn't extracted yet — use
    # plain sha256sum. Glob is safe in a fresh dir, and CWD matches where
    # each .sha256 records its filename.
    sha256sum -c artifactflow-*.tar.gz.sha256
    tar xzf artifactflow-deploy-${VERSION}.tar.gz
    tar xzf artifactflow-config-${VERSION}.tar.gz
    tar xzf artifactflow-forensics-${FORENSICS_SLUG}.tar.gz   # → ./forensics/
    docker load -i artifactflow-infra-${INFRA_SLUG}.tar.gz
    docker load -i artifactflow-app-${VERSION}.tar.gz
    # Forensics: install py-spy to host PATH, install analyst wheels.
    # Both are offline — no network calls on this host.
    sudo install -m 0755 forensics/bin/py-spy /usr/local/bin/py-spy
    pip install --no-index --find-links forensics/wheels pandas
    # Verify host has the kernel-level forensics tools too:
    ./deploy/scripts/preflight.sh
    cp deploy/.env.intranet.example deploy/.env && vi deploy/.env
    AF_VERSION=${VERSION} docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d
    # No pause/resume here — there's nothing running to pause.

  # ---- Roll-update (most common, no infra, no forensics re-ship) ----
  # Forensics tar is content-addressed (pyspy<ver>-py<ver>) — if neither
  # version changed, the previous tar on the target is still valid; skip.
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
