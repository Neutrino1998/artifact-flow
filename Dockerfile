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

COPY requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt

# --- Stage 2: runtime ---
FROM python:3.11-slim

LABEL maintainer="1998neutrino@gmail.com"
LABEL description="ArtifactFlow - Multi-Agent System"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/root/.local/bin:$PATH

# Runtime system deps (pandoc for docx convert, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bring over installed Python deps from builder (~/.local)
COPY --from=builder /root/.local /root/.local

# Source code
COPY . .

# Register the local package (no deps — already installed via builder)
RUN pip install --user -e . --no-deps

# Data directory + entrypoint exec bit
RUN mkdir -p /app/data && chmod +x /app/deploy/entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "8000"]
