#!/usr/bin/env bash
set -euo pipefail
# Run AFTER reboot. Confirms the box came up on the 4K-page kernel.
ps=$(getconf PAGE_SIZE); kr=$(uname -r)
echo "uname -r  : $kr"
echo "PAGE_SIZE : $ps"
case "$kr" in *.4k.*) ;; *) printf '\033[33m! uname 不含 .4k. — 核对是否真为 4K 内核\033[0m\n';; esac
if [ "$ps" = 4096 ]; then
  printf '\033[32m✓ PASS — 已在 4K 页内核。\033[0m 下一步 gVisor: 重跑 gvisor-pkg/smoke-test.sh(上次卡的 page size mismatch 应消失)\n'
else
  printf '\033[31m✗ 仍是 %s 页 — 没进 4K 内核。\033[0m\n' "$ps"
  echo "  当前默认: $(grubby --default-kernel 2>/dev/null || echo '?')"
  echo "  查: grubby --default-kernel 是否指向 ...4k... ;或重启在 GRUB 菜单手选 4k 条目"
  exit 1
fi
