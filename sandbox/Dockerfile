# syntax=docker/dockerfile:1.7
# ^ ADD --checksum 需要 frontend ≥1.6;不钉的话用构建机 BuildKit 内置版本,
#   老 Docker 会在 ADD 行硬报错。钉版会在构建时拉 frontend 镜像 —— 构建必须
#   在联网机做(本文件既有前提),气隙节点只 docker load,不受影响。
#
# ArtifactFlow sandbox image — gVisor (runsc) functional verification (plan §B).
#
# This is the "tier-1 baked environment" (plan 原则 7): interpreter +
# scientific/document stack + LibreOffice + pandoc + ripgrep + zip + git. It runs untrusted
# LLM-generated code under runsc.
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
#   libreoffice — shared Office conversion/render/recalculation engine. Writer,
#             Calc, and Impress cover the common docx/xlsx/pptx workflows; each
#             call uses an isolated profile through artifactflow-office.
#   pandoc  — docx/html↔md canary + doc-conversion driver scenario (C). Debian's
#             pandoc is older than upstream but fine for the md↔docx canary
#             (PDF output needs LaTeX, out-of-scope this phase).
#   ripgrep — FS-traversal syscall probe (getdents64/statx, distinct from the
#             numerical C-ext surface) + general search over mounted files.
#   zip     — archive staged workspaces for persist; tiny generic CLI that
#             matches model/user expectations better than python -m zipfile.
#   git     — LOCAL repo operations only (init/log/diff/blame/apply over mounted
#             or unzipped trees); clone/fetch are dead under --network=none by
#             design. Tier-1 because it's generic + environment-definition and
#             can't be installed at runtime with zero network.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fonts-liberation2 \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    pandoc \
    ripgrep \
    zip \
    git \
    && rm -rf /var/lib/apt/lists/*

# 中文字形(E-4):没有 CJK 字体 matplotlib 中文标签全是豆腐块,gate 所有内网
# 中文图表场景。不用 apt fonts-noto-cjk —— 它只装 .ttc 集合,matplotlib 只注册
# 集合第一个 face(JP,简体字形有日式变体),实测 "Noto Sans CJK SC" 查无此名;
# 直接烤两个 SC 单 .otf(Regular/Bold,共 ~32MB,比 apt 包省 ~60MB)。
# URL 钉 commit SHA + --checksum:mutable main 会让多月后的重建静默换字节/404,
# 且 raw.githubusercontent 直连不走 pip 镜像,构建机代理有抽风案底 —— 字节级钉死。
ADD --checksum=sha256:2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b \
    https://raw.githubusercontent.com/notofonts/noto-cjk/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf \
    /usr/share/fonts/opentype/noto-sc/
ADD --checksum=sha256:b5f0d1a190a7f9b43c310a8850630af12553df32c4c050543f9059732d9b4c0a \
    https://raw.githubusercontent.com/notofonts/noto-cjk/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf \
    /usr/share/fonts/opentype/noto-sc/
RUN chmod 644 /usr/share/fonts/opentype/noto-sc/*.otf

# matplotlib 全局默认:中文字体优先 + 正确的负号渲染。放镜像层而非要求每个
# 脚本各设一遍 rcParams —— 沙盒里任何画图代码(不止 skill 脚本)自动受益。
ENV MATPLOTLIBRC=/opt/sandbox/matplotlibrc
RUN mkdir -p /opt/sandbox && printf '%s\n' \
    'font.sans-serif: Noto Sans CJK SC, DejaVu Sans' \
    'axes.unicode_minus: False' \
    > /opt/sandbox/matplotlibrc

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

# Stable model-facing Office interface. Keep orchestration here instead of in
# each skill bundle: the executable is part of the sandbox runtime, while the
# skills only explain format-specific intent and QA.
COPY office_cli.py /usr/local/bin/artifactflow-office
RUN chmod 755 /usr/local/bin/artifactflow-office

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
