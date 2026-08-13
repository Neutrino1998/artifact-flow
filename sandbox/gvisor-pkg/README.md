# gVisor offline package

`runsc` + `containerd-shim-runsc-v1` (gVisor `release-20260706.0` by default,
architecture selected by `ARCH`) plus
install / smoke / uninstall scripts, for installing gVisor as a Docker runtime
on an air-gapped Kylin node.

The binaries (~46MB) are **not** in git. Reproduce the install tar on a
networked build host:

```bash
ARCH=aarch64 sandbox/gvisor-pkg/fetch-and-package.sh
# → dist/sandbox-gvisor-release-20260706.0-aarch64.tar.gz (+ .sha256)
```

The filename is addressed by the pinned gVisor release and architecture. A
verified existing tar is reused across application releases.

On the intranet node (as root):

```bash
tar xzf sandbox-gvisor-release-20260706.0-aarch64.tar.gz
cd sandbox-gvisor-release-20260706.0-aarch64
sudo ./install.sh                 # verify sha512, install binaries, register runsc in daemon.json
sudo systemctl reload docker      # reload (not restart) — running containers undisturbed
sudo ./smoke-test.sh              # Tier 0–5; Tier 0 = the unshare -U BLOCKED check
```

Withdraw after verification ("验完即撤出"):

```bash
sudo ./uninstall.sh && sudo systemctl reload docker
```

**Tier 0 is a gate, not a formality.** If `unshare -U` fails, the node's kernel
denies `CLONE_NEWUSER` and gVisor cannot run there. `smoke-test.sh` stops; record
its output together with `uname -a`, `getconf PAGE_SIZE`, `runsc --version`, and
the relevant kernel log before escalating to ops/vendor.
