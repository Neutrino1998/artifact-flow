# ArtifactFlow sandbox image — gVisor (runsc) functional verification (plan §B).
#
# This is the "tier-1 baked environment" (plan 原则 7): interpreter + scientific
# stack + pandoc + ripgrep. It runs untrusted LLM-generated code under runsc.
# DECOUPLED from the backend image / requirements.lock — the sandbox is its own
# runtime, not the app. Python deps are pinned in sandbox/requirements.txt.
#
# Build for linux/amd64 (intranet target is x86_64) via scripts/build-sandbox-image.sh.
# Air-gap: everything is fetched HERE on the networked build host and baked in;
# the intranet test node only `docker load`s the saved tar and runs it under
# --runtime=runsc --network=none with ZERO network (plan 原则 7).

FROM python:3.11-slim

LABEL maintainer="1998neutrino@gmail.com"
LABEL description="ArtifactFlow sandbox runtime (gVisor verification, plan §B)"

# MPLBACKEND=Agg — headless matplotlib backend; without it `import
# matplotlib.pyplot` may try to reach an X server, which under runsc/--network=none
# is both wrong and a syscall-surface red herring.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

# System tools baked into tier-1:
#   pandoc  — docx/html↔md canary + doc-conversion driver scenario (C). Debian's
#             pandoc is older than upstream but fine for the md↔docx canary
#             (PDF output needs LaTeX, out-of-scope this phase).
#   ripgrep — FS-traversal syscall probe (getdents64/statx, distinct from the
#             numerical C-ext surface) + general search over mounted files.
#   git     — LOCAL repo operations only (init/log/diff/blame/apply over mounted
#             or unzipped trees); clone/fetch are dead under --network=none by
#             design. Tier-1 because it's generic + environment-definition and
#             can't be installed at runtime with zero network.
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    ripgrep \
    git \
    && rm -rf /var/lib/apt/lists/*

# Baked git defaults — `git commit` hard-errors without user.name/email and the
# sandbox has no interactive way to set one. --system so it applies to uid 1000.
# safe.directory '*': the dubious-ownership check protects multi-user hosts;
# here the container IS the trust boundary, and bind-mounted trees may carry a
# host uid ≠ 1000 that would otherwise make every git op error out.
RUN git config --system user.name "sandbox" && \
    git config --system user.email "sandbox@artifactflow.local" && \
    git config --system init.defaultBranch main && \
    git config --system safe.directory '*'

# Scientific stack — the real ENOSYS workload under runsc (plan §B "验什么").
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Freeze the resolved (transitive) pip set into the image — the sandbox analog
# of release.sh's wheels.lock.txt. build-sandbox-image.sh extracts this into a
# dist/ sidecar so ops can diff bundles without loading the tar.
RUN pip freeze > /opt/sandbox-wheels.lock.txt

# Offline-install fixture (plan §B: verify the tier-2/3 find-links delivery path
# under runsc). Build a trivial pure-Python wheel HERE (networked build host)
# into /opt/stub-wheels so the in-sandbox probe can:
#   pip install --no-index --find-links /opt/stub-wheels af-sandbox-stub
# with zero network — proving offline `pip install` survives Sentry.
COPY stub-pkg /tmp/stub-pkg
RUN pip wheel --no-deps -w /opt/stub-wheels /tmp/stub-pkg && rm -rf /tmp/stub-pkg

# Non-root: untrusted code must not run as root inside the sandbox. Fixed uid
# 1000 + a known-owned /work so the bind-mount uid-mapping check (plan §B) has a
# concrete owner to assert against on the host side.
RUN useradd --create-home --uid 1000 sandbox && \
    mkdir -p /work && chown sandbox:sandbox /work
USER sandbox
WORKDIR /work

# No work-doing ENTRYPOINT/CMD: the §B verification probes are bind-mounted in
# by the host orchestrator (kept editable without a rebuild during B iteration,
# and exercising the bind-mount path itself). Default to an idle interpreter.
CMD ["python3"]
