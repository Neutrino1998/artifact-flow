# ========================================
# ArtifactFlow Docker Image (multi-stage)
# ========================================
# Stage 1 (builder): installs Python deps into ~/.local
# Stage 2 (runtime): copies installed deps + adds pandoc/curl
# Net effect vs single-stage: drops pip cache, build artifacts, and pip's own
# transitive tooling out of the final image.

# --- Stage 1: builder ---
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install from the pinned lockfile (DEP-02), not the abstract requirements.txt,
# so the image audited on the build host == the image deployed (no `>=` drift).
# requirements.lock is regenerated from requirements.txt via pip-compile inside
# this same python:3.11-slim image — see CLAUDE.md "Essential Commands".
COPY requirements.lock .
RUN pip install --user --no-warn-script-location -r requirements.lock

# py-spy: in-container backup attach path for incident forensics. Kept out
# of requirements.txt because it's not a runtime dep — the main process
# never imports it; it's invoked via `docker exec backend py-spy ...` when
# PR-obs-lite's faulthandler deadman dump isn't enough. ~6MB; rides the
# existing `COPY --from=builder /root/.local` path into the runtime image.
# Requires `cap_add: [SYS_PTRACE]` on the backend service to actually
# attach in-container — see deploy/compose.base.yml.
# Pin the diagnostic binary so the immutable application image is reproducible.
RUN pip install --user --no-warn-script-location py-spy==0.4.1

# --- Stage 2: runtime ---
FROM python:3.11-slim

LABEL maintainer="1998neutrino@gmail.com"
LABEL description="ArtifactFlow - Multi-Agent System"

ARG ARTIFACTFLOW_SANDBOX_IMAGE=artifactflow-sandbox:latest
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ARTIFACTFLOW_SANDBOX_IMAGE=${ARTIFACTFLOW_SANDBOX_IMAGE} \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PATH=/root/.local/bin:$PATH

# Runtime system deps (pandoc for docx convert, curl for healthcheck,
# ca-certificates for target-local outbound trust anchors at container start).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    openssl \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring over installed Python deps from builder (~/.local)
COPY --from=builder /root/.local /root/.local

# Runtime inputs are explicit. This keeps image contents and release.sh's
# resume fingerprint aligned: adding a runtime file requires changing this
# list instead of silently riding a broad COPY while an old archive is reused.
COPY requirements.txt requirements.lock ./
COPY pyproject.toml setup.py run_server.py alembic.ini ./
COPY src ./src
# Only scripts invoked by the running service or in-container operators belong
# in the image. Build/release helpers stay in the source/deploy bundle.
COPY scripts/create_admin.py scripts/inspect_tool_failures.py \
     scripts/migrate_users_csv.py scripts/observability_report.py \
     scripts/reconcile_config.py ./scripts/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh

# Register the local package (no deps — already installed via builder). Disable
# build isolation so this local setup.py install does not fetch build deps.
RUN pip install --user -e . --no-deps --no-build-isolation

# Data directory + entrypoint exec bit
RUN mkdir -p /app/data && chmod +x /app/deploy/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]
