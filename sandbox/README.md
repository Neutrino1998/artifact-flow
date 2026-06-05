# Sandbox — gVisor functional verification kit (plan §B)

Everything needed to verify gVisor (`runsc`) on a healthy Kylin node in **one
intranet trip**, then withdraw. Background + decisions:
`docs/_archive/design/sandbox-implementation-plan.md` (§B + 原则 7) and
`docs/_archive/design/sandbox-gvisor-evaluation-2026-05.md`.

```
sandbox/
├── Dockerfile              tier-1 sandbox image (py3.11 + sci stack + pandoc + ripgrep,
│                           non-root uid1000, baked offline-install stub wheel)
├── requirements.txt        sandbox python deps — DECOUPLED from backend requirements.lock
├── stub-pkg/               trivial pure-Python pkg → baked wheel for the offline-install probe
├── docker-pkg/             offline Docker Engine + compose (static) for a BARE node (see its README)
├── gvisor-pkg/             runsc install/smoke/uninstall + fetch-and-package.sh (see its README)
└── verify/                 the five §B probes + run-all.sh orchestrator
scripts/build-sandbox-image.sh   build + docker-save the image tar (mirrors release.sh)
```

## What §B verifies (plan)

| Probe | Where | Checks |
|---|---|---|
| `verify-enosys.py` | in-container | numpy/pandas/matplotlib(PNG+PDF)/Pillow/openpyxl/pypdf — the real ENOSYS gamble; C-ext failure = Firecracker-fallback signal |
| `verify-pandoc.sh` | in-container | docx/html↔md round trip (self-generated fixtures) |
| `verify-offline-install.sh` | in-container | `pip install --no-index --find-links` survives Sentry (tier-2/3 delivery path) |
| `verify-bindmount.sh` | host | container writes → host reads back, uid mapping, ripgrep over the gofer mount |
| `verify-network.sh` | host | `--network=none` isolated; bridge egress + runsc netstack DNS; allowlist = host firewall (documented) |

## Build (networked build host — Mac)

Both arches build the same way; pass `ARCH` / `PLATFORM`. Output names carry the
arch (`-amd64` / `-arm64` / `-aarch64`) so the two sets coexist in `dist/`.

```bash
# x86_64 (default)
sandbox/gvisor-pkg/fetch-and-package.sh                  # → dist/sandbox-gvisor-<date>-x86_64.tar.gz
./scripts/build-sandbox-image.sh                         # → dist/artifactflow-sandbox-<date>-amd64.tar.gz (image)

# arm64 / Kunpeng (Kylin arm) — arm64 builds NATIVE on Apple Silicon (fast)
ARCH=aarch64 sandbox/docker-pkg/fetch-and-package.sh     # → dist/docker-offline-<date>-aarch64.tar.gz  (bare node!)
ARCH=aarch64 sandbox/gvisor-pkg/fetch-and-package.sh     # → dist/sandbox-gvisor-<date>-aarch64.tar.gz
PLATFORM=linux/arm64 ./scripts/build-sandbox-image.sh    # → dist/artifactflow-sandbox-<date>-arm64.tar.gz (image)
# verify tar is arch-agnostic (shared): dist/artifactflow-sandbox-verify-<date>.tar.gz
```

**Transfer units** to the air-gapped node: the **image** tar, the **verify** tar
(shared), the **gVisor package** tar — plus, on a *bare* node, the **docker
offline** tar (engine+compose, since nothing is installed). The probes are NOT
baked into the image (the host-side ones run on the host), so they ride their
own tar.

Arch note: `build-sandbox-image.sh` builds `linux/arm64` NATIVE on Apple Silicon
(fast); `linux/amd64` is QEMU-emulated (slow — a mid-build SSL/EOF is usually the
build-host proxy flapping, not the Dockerfile; just re-run, layer cache is fast).
Local rehearsal off-Kylin: build native arch + run with `RUNTIME=runc` (validates
everything except gVisor-specific syscall behavior). **The §B ENOSYS result is
per-arch — it does NOT transfer x86↔arm; each arch must run `run-all.sh` itself.**

## Run on the intranet node

`<arch>` = `x86_64`/`aarch64` for the gVisor/docker tars, `amd64`/`arm64` for the
image tar + tag. A box is single-arch — use one consistently.

```bash
# 0. BARE node only (no docker): install engine + compose first
tar xzf docker-offline-<date>-<arch>.tar.gz && cd docker-offline-<date>-<arch>
sudo ./install.sh && docker info | grep -i 'server version'; cd ..

# 1. gVisor (as root)
tar xzf sandbox-gvisor-<date>-<arch>.tar.gz && cd sandbox-gvisor-<date>-<arch>
sudo ./install.sh && sudo systemctl reload docker && sudo ./smoke-test.sh; cd ..

# 2. sandbox image + verify probes
gunzip -c artifactflow-sandbox-<date>-<arch>.tar.gz | docker load  # → artifactflow-sandbox:<date>-<arch>
tar xzf artifactflow-sandbox-verify-<date>.tar.gz                  # → ./verify/  (arch-agnostic)

# 3. one-shot verification. Pass the arch'd tag explicitly. Add PROBE_HOST/
#    PROBE_NAME to exercise the network checks, else those skip.
IMAGE=artifactflow-sandbox:<date>-<arch> \
PROBE_HOST=<internal-ip:port> PROBE_NAME=<internal.hostname> \
  bash verify/run-all.sh

# 4. withdraw
cd sandbox-gvisor-<date>-<arch> && sudo ./uninstall.sh && sudo systemctl reload docker; cd ..
docker rmi artifactflow-sandbox:<date>-<arch>
# on a node provisioned just for this: also remove docker
cd docker-offline-<date>-<arch> && sudo PURGE=1 ./uninstall.sh
```

**Done = ** all probes green + the frozen image (tar + image id from the
manifest) + the runtime config (daemon.json runsc entry + the fixed `docker run`
flags exercised here: `--runtime=runsc --network=none`, uid mapping). That image
id is the freeze anchor C builds against.
