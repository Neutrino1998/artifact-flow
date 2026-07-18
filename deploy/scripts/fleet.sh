#!/usr/bin/env bash
set -euo pipefail

# Compatibility bridge for the retired shell Fleet controller. It owns no
# parsing state, lock, release identity, or deployment logic; all mutations go
# through afctl's single state machine.

find_afctl() {
  local dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -n "${AF_ROOT:-}" && -x "$AF_ROOT/bin/afctl" ]]; then
    printf '%s\n' "$AF_ROOT/bin/afctl"
    return
  fi
  while [[ "$dir" != / ]]; do
    if [[ -x "$dir/bin/afctl" ]]; then printf '%s\n' "$dir/bin/afctl"; return; fi
    dir="$(dirname "$dir")"
  done
  echo "afctl is not installed; run the afctl binary carried at the top of a v2 release bundle" >&2
  exit 1
}

AFCTL="$(find_afctl)"
ROOT="${AF_ROOT:-$(cd "$(dirname "$AFCTL")/.." && pwd)}"
sub="${1:-help}"
shift || true

case "$sub" in
  init-local)
    [[ $# -eq 0 ]] || { echo "fleet init-local flags are retired; edit control/site.toml explicitly" >&2; exit 2; }
    exec "$AFCTL" --root "$ROOT" site init --preset intranet
    ;;
  bootstrap)
    [[ $# -eq 1 ]] || { echo "usage: fleet.sh bootstrap BUNDLE" >&2; exit 2; }
    "$AFCTL" --root "$ROOT" doctor
    "$AFCTL" --root "$ROOT" plan apply "$1"
    exec "$AFCTL" --root "$ROOT" apply "$1"
    ;;
  preflight) [[ $# -eq 0 ]] || exit 2; exec "$AFCTL" --root "$ROOT" doctor ;;
  deploy|deploy-config)
    if [[ "${1:-}" == "--dry-run" && $# -eq 2 ]]; then exec "$AFCTL" --root "$ROOT" plan apply "$2"; fi
    [[ $# -eq 1 ]] || { echo "usage: fleet.sh $sub [--dry-run] BUNDLE" >&2; exit 2; }
    exec "$AFCTL" --root "$ROOT" apply "$1"
    ;;
  config) exec "$AFCTL" --root "$ROOT" config "$@" ;;
  status) [[ $# -eq 0 ]] || exit 2; exec "$AFCTL" --root "$ROOT" status ;;
  rollback)
    if [[ $# -eq 0 ]]; then exec "$AFCTL" --root "$ROOT" rollback; fi
    if [[ $# -eq 1 && "$1" == "--dry-run" ]]; then exec "$AFCTL" --root "$ROOT" plan rollback; fi
    echo "usage: fleet.sh rollback [--dry-run]" >&2; exit 2
    ;;
  maintenance) exec "$AFCTL" --root "$ROOT" maintenance "$@" ;;
  help|-h|--help)
    echo "fleet.sh is retired; use: $AFCTL --root $ROOT --help"
    ;;
  *)
    echo "fleet command '$sub' was removed; use afctl's strict command surface" >&2
    exit 2
    ;;
esac
