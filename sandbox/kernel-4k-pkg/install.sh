#!/usr/bin/env bash
set -euo pipefail
# Run as root on the TARGET node AFTER preflight.sh is green.
# Installs the 4K-page kernel ALONGSIDE the running 64K kernel and makes it the
# GRUB default. Reversible: the old kernel is untouched (rollback at the bottom).
# Does NOT reboot — that's the operator's call after the pre-reboot self-check.

[ "$(id -u)" = 0 ] || { echo "需 root: sudo ./install.sh"; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$HERE"
OLD="$(uname -r)"; OLDVM="/boot/vmlinuz-$OLD"   # current 64K kernel = rollback anchor

# --- hard guards (subset of preflight; refuse the footguns) ---
[ "$(uname -m)" = aarch64 ] || { echo "✗ 非 aarch64,拒绝"; exit 1; }
if ! grep -q 'BOOT_IMAGE=' /proc/cmdline && [ "${FORCE:-0}" != 1 ]; then
  echo "✗ /proc/cmdline 无 BOOT_IMAGE(外部内核?),换核不会生效。确信请 FORCE=1 重试。"; exit 1
fi
[ -f SHA256SUMS ] && sha256sum -c SHA256SUMS || { echo "✗ 介质校验失败"; exit 1; }

core=$(ls kernel-core-*.rpm | head -1)
KVER=$(basename "$core"); KVER=${KVER#kernel-core-}; KVER=${KVER%.rpm}
VM="/boot/vmlinuz-$KVER"; IMG="/boot/initramfs-$KVER.img"
echo "=== 安装 4K 内核 $KVER(与当前 $OLD 并存)==="

# --- 1. rpm install ALONGSIDE (never -U/-e: keep the running kernel for rollback) ---
if rpm -q "kernel-core-$KVER" >/dev/null 2>&1; then
  echo "→ kernel-core-$KVER 已安装,跳过 rpm"
else
  # globs are disjoint on purpose: kernel-modules-4* excludes kernel-modules-extra-*,
  # kernel-4* matches only the meta rpm — avoids rpm's "already added, skipping" dup warning.
  rpm -ivh kernel-core-*.rpm kernel-modules-4*.rpm kernel-modules-extra-*.rpm kernel-4*.rpm
fi

# --- 2. ensure vmlinuz + initramfs exist (LVM root cannot boot without initramfs) ---
[ -f "$VM" ] || { echo "✗ 缺 $VM,rpm 未落盘"; exit 1; }
if [ ! -f "$IMG" ]; then echo "→ initramfs 缺失,dracut 生成中..."; dracut -f "$IMG" "$KVER"; fi
[ -f "$IMG" ] || { echo "✗ initramfs 生成失败,勿重启"; exit 1; }

# --- 3. ensure a GRUB entry, then set it default (copy current kernel's args) ---
if ! grubby --info="$VM" >/dev/null 2>&1; then
  echo "→ GRUB 无此条目,grubby --add-kernel..."
  args=$(grubby --info="$OLDVM" | sed -n 's/^args="\(.*\)"$/\1/p')
  grubby --add-kernel="$VM" --initrd="$IMG" --title="Kylin 4K $KVER" --args="$args"
fi
grubby --set-default "$VM"

cat <<EOF

=== 完成。重启前自检(全 OK 才 reboot)===
  vmlinuz  : $([ -f "$VM" ]  && echo OK || echo MISSING)  $VM
  initramfs: $([ -f "$IMG" ] && echo OK || echo MISSING)  $IMG
  default  : $(grubby --default-kernel)
下一步 :  sudo reboot   然后   ./postcheck.sh   (期望 PAGE_SIZE=4096)
回滚   :  sudo grubby --set-default $OLDVM && sudo reboot   (回到 64K $OLD)
         或重启时在 GRUB 菜单手选旧条目
EOF
