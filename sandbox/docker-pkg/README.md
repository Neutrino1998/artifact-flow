# Docker offline package (bare node provisioning)

Install Docker Engine + compose on a **bare, air-gapped** node (e.g. a fresh
Kylin V10 arm with nothing on it) from STATIC binaries — no package mirror, no
dependency resolution. Needed because the §B verification (and later the app)
require docker, but the new nodes ship empty.

Binaries are **not** in git. Reproduce on a networked build host:

```bash
ARCH=aarch64 sandbox/docker-pkg/fetch-and-package.sh   # Kylin arm → dist/docker-offline-<date>-aarch64.tar.gz
ARCH=x86_64  sandbox/docker-pkg/fetch-and-package.sh   # x86 node
```

On the bare node (root):

```bash
tar xzf docker-offline-<date>-<arch>.tar.gz && cd docker-offline-<date>-<arch>
sudo ./install.sh
docker info | grep -i 'server version'   # smoke
docker compose version
```

Then layer gVisor on top:

```bash
# (gVisor package, same trip)
cd ../sandbox-gvisor-<date>-<arch> && sudo ./install.sh && sudo systemctl reload docker
```

Withdraw ("验完即撤"):

```bash
sudo ./uninstall.sh           # binaries + units
sudo PURGE=1 ./uninstall.sh   # also wipe /var/lib/docker (images/containers/volumes)
```

## Provisioning order on a bare Kylin arm node

1. `docker-pkg/install.sh` — engine + compose, starts dockerd
2. `gvisor-pkg/install.sh` + `systemctl reload docker` — registers runsc
3. `gvisor-pkg/smoke-test.sh` — Tier 0 (`unshare -U` gate) … Tier 5
4. `docker load` the **arm64** sandbox image tar, `tar xzf` the verify tar
5. `verify/run-all.sh` under runsc — the §B probes (must re-run on arm; the x86
   ENOSYS result does NOT transfer)

## Kylin V10 gotchas (if `dockerd` won't start)

`journalctl -u docker -u containerd` is the first stop. Common on Kylin:

- **SELinux enforcing** — static dockerd ships no SELinux policy. Quick path for
  a verification node: `setenforce 0` (and persist in `/etc/selinux/config`). For
  production, add a policy instead of disabling.
- **`overlay` kernel module** — `containerd.service` does `modprobe overlay`; if
  the module is absent the storage driver falls back (slow/none). Check `lsmod`.
- **iptables / firewalld** — dockerd manages its own iptables chains; on a locked
  box you may need `firewalld` running (or nft/iptables present) for container
  networking. The §B probes mostly use `--network=none`, so this rarely blocks
  verification, but bridge egress checks need it.
- **cgroup driver** — static dockerd auto-detects v1/v2; no action normally.

These are documented, not automated — a verification node is hand-driven and the
right fix (disable vs policy) is operator's call.
