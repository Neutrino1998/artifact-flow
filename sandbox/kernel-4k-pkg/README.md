# Kylin V10 SP3 arm — 4K-page kernel offline package

Switch a Kylin V10 SP3 **arm64 (鲲鹏)** node from its default **64K-page** kernel to
a **4K-page** kernel, in place, no instance recreate. Needed because **gVisor
(`runsc`) on arm64 requires 4K base pages** — on a 64K host the Sentry refuses to
start (`host page size mismatch - running on non-4K host`), which is exactly what
blocked §B on the Kunpeng box. The vendor ships the 4K kernel as a drop-in RPM
set (not a separate ISO): `https://update.cs2c.com.cn/CS/V10/V10SP3-2403/kernel-4k/`.

Page size is a kernel **compile-time** constant — "switching to 4K" literally means
installing a different kernel binary and rebooting into it. This package installs
it **alongside** the running kernel (the old one stays as a one-keystroke rollback).

## When this applies

Only arm64 Kylin with `getconf PAGE_SIZE` = 65536. x86/C86(海光) already run 4K
pages — they reuse the x86 §B result and need none of this.

`preflight.sh` gates on the one thing that can make this not work: the instance
must boot its **own in-image kernel** (GRUB loads it from disk — `BOOT_IMAGE=` in
`/proc/cmdline`). If a cloud platform injects the kernel from the host instead,
installing one in the guest does nothing → that box needs a different image, not
this package.

## Build (networked build host — Mac/Linux)

Binaries are **not** in git. Reproduce the tar:

```bash
sandbox/kernel-4k-pkg/fetch-and-package.sh                # → dist/sandbox-kernel4k-<date>-89.38.tar.gz
BUILD=89.31 sandbox/kernel-4k-pkg/fetch-and-package.sh    # pin a different 4K build
```

Downloads the 4 boot-essential RPMs (`kernel-core` / `kernel-modules` /
`kernel-modules-extra` / `kernel` meta). The `-devel`/`-headers`/`-tools`/`bpftool`/
`perf` packages in the repo are build-time only and intentionally omitted.

## Run on the Kylin arm node (air-gapped)

```bash
tar xzf sandbox-kernel4k-<date>-<build>.tar.gz && cd sandbox-kernel4k-<date>-<build>

./preflight.sh                 # read-only: arch / page size / BOOT_IMAGE gate / grubby / initramfs / 介质校验
sudo ./install.sh              # rpm 并存安装 + 确保 initramfs + grubby 设默认(不重启)
                               # ↑ 看它打印的「重启前自检」三项全 OK
sudo reboot
./postcheck.sh                 # 期望 PAGE_SIZE=4096
```

Then re-run the gVisor smoke test (`gvisor-pkg/smoke-test.sh`) — the page-size
failure should be gone, and §B on arm proceeds like x86.

## Rollback

The old kernel is never removed. Boot back into it:

```bash
sudo grubby --set-default /boot/vmlinuz-<old 64K version> && sudo reboot
# or just pick the old entry in the GRUB menu at boot
```

## Notes / gotchas

- **Root on LVM** (`root=/dev/mapper/...`, `rd.lvm.lv=...` in cmdline): the new
  kernel's initramfs MUST carry LVM + the disk driver. `rpm` triggers a host-only
  `dracut` automatically; `install.sh` verifies `initramfs-...4k.img` exists and
  regenerates it if not — **never reboot if that file is missing** (LVM root won't
  mount). This is why install is split from reboot.
- **Build number need not match** the running 64K kernel (e.g. 64K=89.11,
  4K=89.38). They are different page-size kernels and coexist; GRUB lists both.
- **No KVM needed** for the 4K path — gVisor's `systrap` platform is userspace
  (ptrace), so a plain VM (no `/dev/kvm`) is fine once pages are 4K.
- **`/boot` space**: one extra kernel set ≈ 50MB (vmlinuz + initramfs). `preflight.sh`
  warns under 80MB free.
