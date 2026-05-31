#!/usr/bin/env bash
# Toggle maintenance mode — shared by Mode 2 (Caddy) and Mode 3 (nginx).
#
# Usage:
#   maintenance.sh on  ["运维说明文案"]   # enable, optional note
#   maintenance.sh off                    # disable
#   maintenance.sh status                 # report state
#
# Mechanism: writes/removes a flag file under deploy/maintenance/. The proxy
# stat's it per-request (nginx `if (-f ...)` / Caddy `file` matcher) — no reload.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAINT_DIR="$(cd "$SCRIPT_DIR/../maintenance" && pwd)"
FLAG="$MAINT_DIR/MAINTENANCE_ON"
NOTE="$MAINT_DIR/note.txt"

cmd="${1:-}"
case "$cmd" in
  on)
    if [[ $# -ge 2 && -n "$2" ]]; then
      printf '%s\n' "$2" > "$NOTE"
    else
      : > "$NOTE"  # empty → page falls back to default text
    fi
    : > "$FLAG"
    echo "✓ 维护模式已开启"
    [[ -s "$NOTE" ]] && echo "  说明：$(cat "$NOTE")"
    ;;
  off)
    rm -f "$FLAG" "$NOTE"
    echo "✓ 维护模式已关闭"
    ;;
  status)
    if [[ -f "$FLAG" ]]; then
      echo "● 维护中"
      [[ -s "$NOTE" ]] && echo "  说明：$(cat "$NOTE")"
    else
      echo "○ 正常运行"
    fi
    ;;
  ""|-h|--help|help)
    sed -n '2,11p' "$0"
    exit 0
    ;;
  *)
    echo "未知命令：$cmd" >&2
    echo "用法：$0 {on [文案] | off | status}" >&2
    exit 2
    ;;
esac
