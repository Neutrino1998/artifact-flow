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

## Build (networked build host — Mac + QEMU)

```bash
sandbox/gvisor-pkg/fetch-and-package.sh        # → dist/sandbox-gvisor-<date>.tar.gz
./scripts/build-sandbox-image.sh               # → dist/artifactflow-sandbox-<date>.tar.gz  (image)
                                               #   dist/artifactflow-sandbox-verify-<date>.tar.gz  (probes)
                                               #   + .sha256 / .wheels.lock / .manifest.txt
```

**Three transfer units** go to the air-gapped node: the **gVisor package** tar,
the **image** tar, and the **verify** tar. The probes are NOT baked into the
image (the host-side ones must run on the host), so they ride their own tar.

`build-sandbox-image.sh` cross-builds `linux/amd64` (intranet is x86_64); on
Apple Silicon that's QEMU — slow, and a mid-build SSL/EOF is usually the
build-host proxy flapping, not the Dockerfile (just re-run; layer cache makes it
fast). Local rehearsal off-Kylin: build native arch and run the kit with
`RUNTIME=runc` (validates everything except gVisor-specific syscall behavior).

## Run on the intranet node

```bash
# 1. gVisor (as root)
tar xzf sandbox-gvisor-<date>.tar.gz && cd sandbox-gvisor-<date>
sudo ./install.sh && sudo systemctl reload docker && sudo ./smoke-test.sh

# 2. sandbox image + verify probes
gunzip -c artifactflow-sandbox-<date>.tar.gz | docker load   # → artifactflow-sandbox:<date>
tar xzf artifactflow-sandbox-verify-<date>.tar.gz            # → ./verify/

# 3. one-shot verification. Pass the versioned tag explicitly (a saved tar
#    carries :<date>; :latest only resolves if this build saved it too). Add
#    PROBE_HOST/PROBE_NAME to exercise the network checks, else those 3 skip.
IMAGE=artifactflow-sandbox:<date> \
PROBE_HOST=<internal-ip:port> PROBE_NAME=<internal.hostname> \
  bash verify/run-all.sh

# 4. withdraw
cd sandbox-gvisor-<date> && sudo ./uninstall.sh && sudo systemctl reload docker
docker rmi artifactflow-sandbox:<date>
```

**Done = ** all probes green + the frozen image (tar + image id from the
manifest) + the runtime config (daemon.json runsc entry + the fixed `docker run`
flags exercised here: `--runtime=runsc --network=none`, uid mapping). That image
id is the freeze anchor C builds against.
