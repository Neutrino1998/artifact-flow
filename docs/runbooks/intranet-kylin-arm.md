# Intranet Kylin ARM Runbook

This runbook records the current intranet deployment target facts for the
Kylin ARM machines. It is the short operational view: what is already known,
what must be checked again, and what must be true before production rollout.

## Target

- Deployment target: two Kylin V10 SP3 ARM / Kunpeng hosts, 16c / 32G. One
  known hostname is `ai-agent-app`.
- Role: sandbox-enabled ArtifactFlow app hosts, with later two-host HA
  validation.
- The old CentOS 7 host `bsyshealthyapc` is retired for this deployment. Do not
  apply its Docker, NTP, or network conclusions to these Kylin hosts unless
  re-verified on the Kylin machines.

After the 2026-06-12 sandbox smoke trip, the hosts were cleaned of the
temporary application media, images, volumes, loop pool, and fstab entries. The
expected residue is only:

- a 4K-page Kylin kernel installed and selected
- `runsc` registered in Docker's daemon config

Treat the formal deployment as a fresh media transfer.

## Hard Facts

- Kylin V10 SP3 ARM defaults to a 64K page-size kernel. gVisor on arm64 rejects
  that host shape with `host page size mismatch - running on non-4K host`.
- `ai-agent-app` was verified as UEFI + LVM root + an in-image GRUB kernel
  (`BOOT_IMAGE=` appears in `/proc/cmdline`), so it can switch kernel in place.
  A cloud image whose kernel is injected externally must be handled by changing
  the image, not by installing guest RPMs.
- The 4K swap path is: install the vendor 4K kernel RPMs beside the old kernel,
  let `grubby` select the 4K entry, reboot, then confirm `PAGE_SIZE=4096`.
  The old 64K kernel remains available for rollback.
- gVisor uses the userspace `systrap` platform here; it does not need `/dev/kvm`.
- The arm smoke path verified `89.11(64K) -> 89.38.4k`, then `run-all.sh` passed.

## Production Blocker

Do not run production on the current split-root disk layout.

The target image had a 100G `vda` split into many XFS/LVM filesystems; the
important constraint was `/var` at about 8G while Docker's data-root lived
there, with essentially no free VG space to reshuffle. That is enough for a
short smoke test, not for Postgres, images, and sandbox scratch.

Before production, each host needs a new data disk mounted at `/data`. The
2026-07 deployment used a 500G disk because the stock 100G `vda` was split
across many small filesystems and `/var` was only about 8G.
Use it for:

- Docker data-root (`/data/docker`), before loading images
- sandbox loop pool and scratch root
- bundled Postgres storage, either through Docker's moved data-root or an
  explicit `/data` bind if the deployment chooses that layout

Recommended target paths:

```bash
/data/docker
/data/artifactflow/sandbox
/data/artifactflow/postgres
```

Check and set Docker's data-root before loading release images:

```bash
docker info | grep -i 'Docker Root Dir'
# /etc/docker/daemon.json should include:
#   "data-root": "/data/docker"
sudo systemctl restart docker
docker info | grep -i 'Docker Root Dir'   # expect /data/docker
```

## Build Media

Build a normal application update for an already-provisioned ARM host. Every
release includes the content-addressed Sandbox image; an app-only release
reuses the runsc runtime and infrastructure images already installed on the
host:

```bash
./scripts/release.sh <version> --platform linux/arm64 --app-only
```

For the first deployment, include the pinned Caddy, PostgreSQL, and Redis
images:

```bash
./scripts/release.sh <version> --platform linux/arm64 --with-infra
```

Docker, runsc, and the scratch filesystem are host prerequisites rather than
release contents. For a bare Kylin host, build and transfer the offline Docker
package:

```bash
ARCH=aarch64 sandbox/docker-pkg/fetch-and-package.sh
```

If the host is still on a 64K kernel, build and transfer the Kylin 4K kernel
package:

```bash
sandbox/kernel-4k-pkg/fetch-and-package.sh
```

The gVisor and Docker packages and the Sandbox image are architecture-specific.
Use `aarch64` for gVisor / Docker packages and `linux/arm64` for release builds.
The gVisor tar is named by pinned runtime version + architecture and can be
reused across app releases, but installing it remains an explicit commissioning
step.

## Host Preflight

Run these on each target host before deployment:

```bash
uname -m                 # expect aarch64
getconf PAGE_SIZE        # expect 4096 before enabling runsc sandbox
grep -o 'BOOT_IMAGE=[^ ]*' /proc/cmdline || true
docker info
docker compose version
docker info --format '{{json .Runtimes}}' | grep runsc
df -h /data
```

If `PAGE_SIZE` is `65536`, run the kernel package in this order:

```bash
tar xzf sandbox-kernel4k-<date>-<build>.tar.gz
cd sandbox-kernel4k-<date>-<build>
./preflight.sh
sudo ./install.sh
sudo reboot
./postcheck.sh            # expect PAGE_SIZE=4096
```

If Docker is absent, install the offline Docker package first, then install
gVisor and reload Docker:

```bash
tar xzf docker-offline-<date>-aarch64.tar.gz
cd docker-offline-<date>-aarch64
sudo ./install.sh
docker compose version

cd ../sandbox-gvisor-release-<version>-aarch64
sudo ./install.sh
sudo systemctl reload docker
sudo ./smoke-test.sh
```

## Site Initialization and Sandbox Configuration

Keep transferred release bundles separate from target-local control state:

```bash
/root/workspace/tmp/<version>     # release tar/.sha256/manifest bundle
/opt/artifactflow/control         # site.toml, .env, certs, site content
/opt/artifactflow/.artifactflow   # immutable releases and state.json
```

Provision `/data/artifactflow/sandbox` as a dedicated mounted filesystem before
initializing the site. `afctl` validates this mount but deliberately does not
create a loop device, format storage, edit `/etc/fstab`, or install runsc.

Initialize a fresh intranet target with the release's own controller:

```bash
sudo /root/workspace/tmp/<version>/afctl \
  --root /opt/artifactflow site init --preset intranet
```

For an existing Fleet v1 installation with `/opt/artifactflow/deploy/.env`, use
`site migrate-v1 --preset intranet --sandbox-runtime runsc` instead; initialization
will refuse to overwrite the legacy credentials.

Keep the generated `control/site.toml` aligned with the host:

```toml
executor = "local"
tls = "static"
infra = "bundled"
sandbox_runtime = "runsc"
scratch_root = "/data/artifactflow/sandbox"
backend_replicas = 2
```

Then place the static certificate under `control/certs/` and fill the internal
model credentials generated in `control/.env`:

```bash
GPUSTACK_DEEPSEEK_API_KEY=
GPUSTACK_VISION_API_KEY=
```

For a two-backend deployment on a 16c / 32G host, start with total engine
concurrency around 32 by setting per-backend concurrency to 16 in
`control/.env`:

```bash
ARTIFACTFLOW_MAX_CONCURRENT_TASKS=16
ARTIFACTFLOW_REDIS_MAX_CONNECTIONS=32
ARTIFACTFLOW_DATABASE_POOL_SIZE=5
ARTIFACTFLOW_DATABASE_MAX_OVERFLOW=10
```

Validate the complete target state before applying the release:

```bash
sudo /root/workspace/tmp/<version>/afctl --root /opt/artifactflow site validate
sudo /root/workspace/tmp/<version>/afctl --root /opt/artifactflow doctor
sudo /root/workspace/tmp/<version>/afctl --root /opt/artifactflow \
  plan apply /root/workspace/tmp/<version>
sudo /root/workspace/tmp/<version>/afctl --root /opt/artifactflow \
  apply /root/workspace/tmp/<version>
```

Sandbox is always part of an `afctl` deployment. The Backend receives the
Docker socket and the same absolute scratch path on host and container; the
strict `site.toml` runtime and scratch fields are the single source of truth.
Production uses `runsc`; `runc` is only an explicit reduced-isolation choice
for trusted development targets.

## Previously Verified

The 2026-06-12 ARM smoke on `ai-agent-app` verified:

- `run-all.sh ALL PASSED`, including the git and dubious-ownership probes
- sandbox image anchor `sha256:fac22b8384e2a6b84915794bd46a79e01b9d9a90df6bf5ab7536b37ee453d08e`
- `artifactflow-sandbox:20260612-arm64`, including git 2.47.3
- DooD smoke 5/5 with backend container root and sandbox uid 1000 ownership
  behavior checked on real Linux
- Word flow: upload docx -> mount -> pandoc -> persist -> download
- watchdog quota kill
- cancel -> sandbox container removal

The x86 sandbox anchor is intentionally deferred until a suitable x86 runsc
host with a new enough kernel exists.

## Network Notes

The fixed Docker subnet `192.168.222.0/24` in `deploy/compose.base.yml` on the
intranet branch was
introduced for the retired CentOS host because that machine conflicted with
corporate `172.16/12` routes. Keep it for now, but treat it as inherited
deployment-specific configuration rather than a proven Kylin requirement.

Before the first production cutover on the Kylin hosts, confirm the selected
Docker subnet does not overlap host routes, libvirt bridges, Kubernetes /
Calico ranges, or corporate client/server ranges.

## Go / No-Go

- [ ] Both target hosts are the intended Kylin ARM machines, not the retired
  CentOS 7 test host.
- [ ] `uname -m` is `aarch64`.
- [ ] `getconf PAGE_SIZE` is `4096`.
- [ ] Docker and Compose are installed from the controlled offline package or
  otherwise verified.
- [ ] `runsc` is registered and `smoke-test.sh` passes.
- [ ] A `/data` disk is mounted and has enough space for Docker, Postgres, and
  sandbox scratch.
- [ ] The release bundle is built with `--platform linux/arm64`.
- [ ] `/data/artifactflow/sandbox` is a dedicated mounted filesystem and matches
      `control/site.toml` exactly.
- [ ] The fixed Docker subnet has been checked against this host's actual
      routing table.
- [ ] `site validate`, `doctor`, and `plan apply` pass before `apply`.
