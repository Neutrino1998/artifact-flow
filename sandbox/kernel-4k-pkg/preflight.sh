#!/usr/bin/env bash
set -euo pipefail
# Read-only. Run on the TARGET Kylin arm node BEFORE install.sh.
# Verdict: can this box switch to a 4K-page kernel IN PLACE (no recreate)?
# The decisive gate is "BOOT_IMAGE= in /proc/cmdline" → the kernel is loaded by
# the guest's own GRUB from disk, so installing a new kernel actually takes effect.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0; warn=0
ok(){  printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad(){ printf '  \033[31m✗\033[0m %s\n' "$*"; fail=1; }
wrn(){ printf '  \033[33m!\033[0m %s\n' "$*"; warn=1; }

echo "=== 4K kernel preflight (read-only) ==="

# 1. arch — this media is aarch64 only (x86 Kylin already runs 4K pages)
a=$(uname -m)
[ "$a" = aarch64 ] && ok "arch = aarch64" || bad "arch = $a(这套 4K 内核只针对 aarch64/鲲鹏)"

# 2. current page size
ps=$(getconf PAGE_SIZE)
if   [ "$ps" = 65536 ]; then ok "PAGE_SIZE = 65536(64K,符合换核前提)"
elif [ "$ps" = 4096  ]; then wrn "PAGE_SIZE 已是 4096 — 可能已在 4K 内核,无需再换"
else wrn "PAGE_SIZE = $ps(异常)"; fi

# 3. THE GATE — in-image kernel?
if grep -q 'BOOT_IMAGE=' /proc/cmdline; then
  ok "/proc/cmdline 有 BOOT_IMAGE → GRUB 从本机磁盘加载内核(自带内核启动,换核会生效)"
else
  bad "/proc/cmdline 无 BOOT_IMAGE → 疑似宿主/外部注入内核,装 4K 内核不生效;此机需换镜像/重建,勿继续"
fi

# 4. UEFI/BIOS — informational, grubby handles both
[ -d /sys/firmware/efi ] && ok "UEFI 引导" || echo "  · BIOS/other 引导(grubby 同样兼容)"

# 5. tooling
command -v grubby >/dev/null && ok "grubby 可用" || bad "缺 grubby(无法设默认启动项)"
command -v rpm    >/dev/null && ok "rpm 可用"    || bad "缺 rpm"
command -v dracut >/dev/null && ok "dracut 可用" || wrn "缺 dracut(LVM 根需要它生成 initramfs)"

# 6. root on LVM? initramfs must carry lvm + disk driver (dracut host-only does)
root_src=$(findmnt -no SOURCE / 2>/dev/null || echo '?')
if printf '%s' "$root_src" | grep -qiE 'mapper|/dev/dm-' || grep -q 'rd.lvm.lv' /proc/cmdline; then
  wrn "根在 LVM($root_src)→ 装后务必确认 initramfs-...4k.img 已生成再重启(install.sh 会查)"
else
  ok "根 = $root_src"
fi

# 7. /boot free space — need room for one more kernel set (vmlinuz+initramfs ~50MB)
avail=$(df -Pk /boot 2>/dev/null | awk 'NR==2{print $4}' || echo '')
if [ -n "$avail" ] && [ "$avail" -ge 80000 ]; then ok "/boot 余量 $((avail/1024))MB(≥80MB)"
else wrn "/boot 余量 ${avail:-?}KB 偏小,再塞一套内核可能不够"; fi

# 8. media integrity + target version
if [ -f "$HERE/SHA256SUMS" ]; then
  if ( cd "$HERE" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ); then ok "介质 SHA256 校验通过"
  else bad "介质 SHA256 校验失败 — 重新传输"; fi
else wrn "无 SHA256SUMS,跳过介质校验"; fi

core=$(ls "$HERE"/kernel-core-*.rpm 2>/dev/null | head -1 || true)
if [ -n "$core" ]; then
  kver=$(basename "$core"); kver=${kver#kernel-core-}; kver=${kver%.rpm}
  echo "  · 将安装内核版本: $kver"
  rpm -q "kernel-core-$kver" >/dev/null 2>&1 && wrn "该 4K 内核已安装,install.sh 会跳过 rpm、只设默认" || true
else bad "目录内找不到 kernel-core-*.rpm"; fi
echo "  · 当前运行内核: $(uname -r)"

echo "==============================================="
if   [ "$fail" = 1 ]; then printf '\033[31m结论: 不可继续(见 ✗)。\033[0m\n'; exit 1
elif [ "$warn" = 1 ]; then printf '\033[33m结论: 可继续,但留意 ! 项。\033[0m 下一步: sudo ./install.sh\n'
else printf '\033[32m结论: 绿灯。\033[0m 下一步: sudo ./install.sh\n'; fi
