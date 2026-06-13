# gVisor offline package

`runsc` + `containerd-shim-runsc-v1` (gVisor `release-20260504.0`, x86_64) plus
install / smoke / uninstall scripts, for installing gVisor as a Docker runtime
on an air-gapped Kylin node.

The binaries (~46MB) are **not** in git. Reproduce the install tar on a
networked build host:

```bash
sandbox/gvisor-pkg/fetch-and-package.sh          # → dist/sandbox-gvisor-<date>.tar.gz (+ .sha256)
```

On the intranet node (as root):

```bash
tar xzf sandbox-gvisor-<date>.tar.gz && cd sandbox-gvisor-<date>
sudo ./install.sh                 # verify sha512, install binaries, register runsc in daemon.json
sudo systemctl reload docker      # reload (not restart) — running containers undisturbed
sudo ./smoke-test.sh              # Tier 0–5; Tier 0 = the unshare -U BLOCKED check
```

Withdraw after verification ("验完即撤出"):

```bash
sudo ./uninstall.sh && sudo systemctl reload docker
```

**Tier 0 is a gate, not a formality.** If `unshare -U` fails, the node's kernel
denies `CLONE_NEWUSER` and gVisor cannot run there — `smoke-test.sh` stops and
points at the eval doc §5.3 evidence pack for ops/vendor. See
`docs/_archive/design/sandbox-gvisor-evaluation-2026-05.md`.
