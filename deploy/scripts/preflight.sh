#!/usr/bin/env bash
# preflight.sh — verify forensics readiness on an intranet host.
#
# Runs OFFLINE (no network calls). Two tiers of checks:
#
#   1. REQUIRED (hard failures, exit 1):
#      - backend container has py-spy (`docker compose ... exec backend py-spy --version`)
#      - analyst-tools/ bundle integrity — only when its path is given
#        explicitly OR the default ./analyst-tools exists. analyst-tools is
#        for the analyst machine (running observability_report.py), which
#        may be a separate host; if absent at the default path and the
#        operator didn't pass one, that's treated as "intentionally on
#        another machine" and skipped with `info`, not failed. Pass an
#        explicit path to force the strict check.
#
#   2. OPTIONAL (warnings, do NOT block exit):
#      - Host deep-dive tools: gdb / gcore / strace / ps / top in PATH.
#      - Yama ptrace_scope: warn only if mode 3 (host-wide ptrace disabled)
#        — that's the one host-side knob that can defeat the in-container
#        backup attach path even with cap_add: SYS_PTRACE, because Yama is
#        host kernel state shared into the container. Modes 0/1/2 are all
#        bypassed by CAP_SYS_PTRACE (which our compose grants).
#
#      Why no CapEff bit / seccomp probe: those are declared by our compose
#      files; if cap_add is dropped, that's a deliberate operator edit, not
#      a runtime drift. Re-validating compose declarations from inside the
#      container is over-engineering — `docker compose config | grep cap_add`
#      already shows the truth.
#
# Exit code: 0 = required all pass (optional warnings allowed);
#            1 = at least one required check failed.
#
# Usage:
#   deploy/scripts/preflight.sh                          # default: ./analyst-tools (lenient)
#   deploy/scripts/preflight.sh /opt/af/analyst-tools    # explicit path (strict)
#
# Output style mirrors verify-bundle.sh: per-check ✓/✗/⚠ + tier counters.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# An explicit positional arg flips analyst-tools into strict mode (missing dir = err).
# Default path = lenient (missing dir = info-skip, deploy may rely on a separate analyst host).
ANALYST_DIR="${1:-$ROOT/analyst-tools}"
ANALYST_EXPLICIT=$([[ -n "${1:-}" ]] && echo yes || echo no)

required_fail=0
optional_warn=0
ok()   { printf '  ✓ %s\n' "$1"; }
err()  { printf '  ✗ %s\n' "$1"; required_fail=$((required_fail + 1)); }
warn() { printf '  ⚠ %s\n' "$1"; optional_warn=$((optional_warn + 1)); }
info() { printf '      %s\n' "$1"; }

# ============================================================================
# REQUIRED #1: backend container has py-spy (in-container backup attach path)
# ============================================================================
echo "→ [required] backend container has py-spy (backup attach path)"
if ! command -v docker >/dev/null 2>&1; then
  # Pre-`docker compose up` first-deploy preflight: docker may be reachable
  # but the backend container may not exist yet. Don't fail — just inform.
  info "docker not on PATH; skipping container py-spy check"
  info "(re-run preflight after \`docker compose up -d backend\` to verify)"
elif ! backend_ctr=$(docker ps --format '{{.Names}}' 2>/dev/null | grep backend | head -1) \
     || [[ -z "$backend_ctr" ]]; then
  info "no running 'backend' container; skipping container py-spy check"
  info "(re-run preflight after \`docker compose up -d backend\` to verify)"
else
  # `docker exec` requires container to be running; py-spy --version exits 0
  # on success, prints to stderr by default. Capture both.
  if output=$(docker exec "$backend_ctr" py-spy --version 2>&1); then
    ok "py-spy in backend container '$backend_ctr' ($output)"
  else
    err "py-spy missing or not invocable inside backend container '$backend_ctr'"
    info "expected: \`docker exec $backend_ctr py-spy --version\` → 'py-spy <version>'"
    info "actual:   $output"
    info "fix: rebuild backend image; Dockerfile installs py-spy in builder stage"
  fi
fi

# ============================================================================
# OPTIONAL: Yama ptrace_scope (host-side; can defeat in-container backup path)
# ============================================================================
# Placed near the py-spy check because semantically it's the one host-side
# knob that gates the backup attach path. mode 3 is the only blocking value
# (modes 0/1/2 are all bypassed by CAP_SYS_PTRACE which our compose grants).
# Warn-not-err: faulthandler primary path stays available regardless, and
# preflight can't fix sysctl anyway — only host operator can.
echo
echo "→ [optional] Yama ptrace_scope (host sysctl, gates backup attach path)"
if [[ -r /proc/sys/kernel/yama/ptrace_scope ]]; then
  yama=$(tr -d '[:space:]' < /proc/sys/kernel/yama/ptrace_scope)
  case "$yama" in
    0|1|2)
      ok "yama.ptrace_scope=$yama (CAP_SYS_PTRACE bypasses; backup path OK)" ;;
    3)
      warn "yama.ptrace_scope=3 — host-wide ptrace disabled, py-spy backup path inert"
      info "primary path (faulthandler deadman → docker logs) still works"
      info "to restore backup: have host operator set kernel.yama.ptrace_scope=1 (or 0/2)"
      ;;
    *)
      info "yama.ptrace_scope=$yama (unexpected value; treat as unknown)"
      ;;
  esac
else
  info "/proc/sys/kernel/yama/ptrace_scope not readable"
  info "(non-Linux host / Yama LSM disabled — fine on dev machines)"
fi

# ============================================================================
# REQUIRED #2 (conditional): analyst-tools bundle integrity
# ============================================================================
echo
echo "→ [required-if-present] analyst-tools bundle integrity ($ANALYST_DIR)"

if [[ ! -d "$ANALYST_DIR" ]]; then
  if [[ "$ANALYST_EXPLICIT" == "yes" ]]; then
    err "analyst-tools/ directory not found at $ANALYST_DIR (explicit path)"
    info "extract: tar xzf artifactflow-analyst-tools-*.tar.gz"
    info "(creates ./analyst-tools/{wheels,README.md,wheels.lock.txt})"
  else
    info "no analyst-tools/ at default path; skipping (analyst machine may be elsewhere)"
    info "to enforce: deploy/scripts/preflight.sh /path/to/analyst-tools"
  fi
else
  WHEELS="$ANALYST_DIR/wheels"
  if [[ ! -d "$WHEELS" ]]; then
    err "wheels/ directory missing: $WHEELS"
  else
    wheel_count=$(find "$WHEELS" -maxdepth 1 -name '*.whl' 2>/dev/null | wc -l | tr -d ' ')
    if [[ "$wheel_count" -gt 0 ]]; then
      ok "wheels present ($wheel_count files)"
    else
      err "wheels/ contains no *.whl files"
    fi

    # Dry-run install — exercises the actual resolver against offline wheels.
    # Catches "wheel built for wrong Python ABI" early.
    # --ignore-installed: forces pip to plan from --find-links instead of
    # short-circuiting on system-installed pandas (dev/build host case).
    if command -v pip >/dev/null 2>&1 || command -v pip3 >/dev/null 2>&1; then
      pip_bin=$(command -v pip || command -v pip3)
      if output=$("$pip_bin" install --no-index --find-links "$WHEELS" \
                    --ignore-installed --dry-run pandas 2>&1); then
        ok "pip install --no-index pandas resolves offline"
      else
        err "pip can't resolve pandas from $WHEELS — wheel/Python mismatch?"
        info "head of pip output:"
        printf '%s\n' "$output" | head -5 | sed 's/^/        /'
      fi
    else
      info "pip not found on this host; skipping offline-resolve check"
      info "(analyst machine running observability_report.py needs pip + same wheels)"
    fi
  fi
fi

# ============================================================================
# OPTIONAL: host deep-dive tools (informational; does NOT block deployment)
# ============================================================================
echo
echo "→ [optional] host deep-dive forensics tools (PATH lookup, warning only)"
echo "  Primary path: \`docker compose ... logs backend\` (faulthandler dump, PR-obs-lite)"
echo "  Backup path:  \`docker compose ... exec backend py-spy ...\` (req'd above)"
echo "  Deep-dive:    host gdb/strace/procps — useful but not blocking"
for tool in gdb gcore strace ps top; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool ($(command -v "$tool"))"
  else
    warn "$tool not in PATH"
    case "$tool" in
      gdb|gcore)
        info "install: 'yum install gdb' (RHEL/CentOS) or 'apt install gdb' (Debian/Ubuntu)" ;;
      strace)
        info "install: 'yum install strace' or 'apt install strace'" ;;
      ps|top)
        info "install: 'yum install procps-ng' (RHEL) or 'apt install procps' (Debian)" ;;
    esac
  fi
done

echo
if (( required_fail > 0 )); then
  echo "✗ Preflight failed — $required_fail required issue(s)"
  if (( optional_warn > 0 )); then
    echo "  ($optional_warn optional warning(s) — fix required issues first)"
  fi
  echo "  Address required issues above before continuing deployment."
  exit 1
fi

if (( optional_warn > 0 )); then
  echo "✓ Preflight passed — bundle ready, backend container forensics OK"
  echo "  ⚠ $optional_warn optional warning(s) — host deep-dive path narrowed"
  echo "  (deployment OK; coordinate with infra to install missing host tools"
  echo "   if you expect to need strace/gdb-level debugging)"
else
  echo "✓ Preflight passed — all required + optional checks OK"
fi
exit 0
