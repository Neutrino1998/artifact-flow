#!/usr/bin/env bash
# preflight.sh — verify forensics readiness on an intranet host.
#
# Runs OFFLINE (no network calls). Two layers of checks:
#
#   1. Host kernel-level tools: gdb / gcore / strace / ps  must all be in PATH.
#      These can't live in the app image (would bloat backend Docker layer + we
#      want forensics that work against the host process, not from inside the
#      container's mount-ns). Distribution-specific install hint emitted on miss.
#
#   2. Forensics bundle integrity: forensics/ tree extracted at expected path,
#      py-spy binary executable and pinned-SHA matches, wheels dir non-empty
#      and pandas install resolvable via `pip install --no-index --dry-run`.
#
# Exit code: 0 = all OK, non-zero = at least one check failed.
#
# Usage:
#   deploy/scripts/preflight.sh                     # default forensics path: ./forensics
#   deploy/scripts/preflight.sh /opt/af/forensics   # explicit path
#
# Output style mirrors verify-bundle.sh: per-check ✓/✗ + a fail counter.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

FORENSICS_DIR="${1:-$ROOT/forensics}"

# SHA verification uses the bundle's own forensics/bin/py-spy.sha256 file
# (written by release.sh after binary extraction). Same checksum file format
# as sha256sum(1), zero drift surface between build-side pin and preflight
# verification.

fail=0
ok()   { printf '  ✓ %s\n' "$1"; }
err()  { printf '  ✗ %s\n' "$1"; fail=$((fail + 1)); }
info() { printf '      %s\n' "$1"; }

echo "→ Host kernel-level forensics tools (PATH lookup)"
# `procps` is a Debian/Ubuntu package, on RHEL family it's `procps-ng`.
# Both ship /bin/ps. We check for the user-visible binaries rather than
# package names, which keeps the check distribution-agnostic.
for tool in gdb gcore strace ps top; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool ($(command -v "$tool"))"
  else
    err "$tool not found in PATH"
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
echo "→ Forensics bundle integrity ($FORENSICS_DIR)"

if [[ ! -d "$FORENSICS_DIR" ]]; then
  err "forensics/ directory not found at $FORENSICS_DIR"
  info "extract: tar xzf artifactflow-forensics-*.tar.gz   (lays out forensics/{bin,wheels,README.md})"
  echo
  echo "✗ Forensics readiness check failed ($fail issue(s))"
  exit 1
fi

# ---- py-spy binary ----
PYSPY="$FORENSICS_DIR/bin/py-spy"
PYSPY_SHA_FILE="$FORENSICS_DIR/bin/py-spy.sha256"
if [[ ! -f "$PYSPY" ]]; then
  err "py-spy binary missing: $PYSPY"
elif [[ ! -x "$PYSPY" ]]; then
  err "py-spy not executable: $PYSPY"
  info "fix: chmod +x '$PYSPY'"
else
  if version=$("$PYSPY" --version 2>&1); then
    ok "py-spy executable ($version)"
  else
    err "py-spy --version failed: $version"
  fi

  # SHA: re-verify the binary against the build-side pin shipped inside the
  # bundle. Catches tamper during transit (scp) or local edits.
  if [[ -f "$PYSPY_SHA_FILE" ]]; then
    if (cd "$FORENSICS_DIR/bin" && sha256sum -c py-spy.sha256 >/dev/null 2>&1); then
      ok "py-spy SHA matches bundle's py-spy.sha256"
    else
      err "py-spy SHA mismatch — possible tamper, partial scp, or local edit"
      info "expected (from bundle): $(awk '{print $1}' "$PYSPY_SHA_FILE")"
      info "actual:                 $(sha256sum "$PYSPY" 2>/dev/null | awk '{print $1}')"
    fi
  else
    err "py-spy.sha256 missing alongside binary: $PYSPY_SHA_FILE"
    info "release.sh writes this — bundle may be from an old release. Re-extract."
  fi

  # py-spy installed to host PATH? Recommend but don't fail — some operators
  # invoke it directly from forensics/bin.
  if command -v py-spy >/dev/null 2>&1; then
    ok "py-spy on PATH ($(command -v py-spy))"
  else
    info "tip: 'sudo install -m 0755 $PYSPY /usr/local/bin/py-spy' for faster invocation"
  fi
fi

# ---- wheels dir ----
WHEELS="$FORENSICS_DIR/wheels"
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
  # Catches "wheel built for wrong Python ABI" early. Falls back gracefully
  # if pip is missing (analyst host may not have pip; that's not a forensics
  # bundle problem).
  #
  # --ignore-installed: without it, pip reports "Requirement already satisfied"
  # when pandas is system-installed (dev/build host) and our check trivially
  # passes without exercising the offline wheels. With it, pip is forced to
  # plan a fresh install from --find-links, which is what runs on the target
  # host that doesn't have pandas yet.
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
  fi
fi

echo
if (( fail )); then
  echo "✗ Preflight failed ($fail issue(s))"
  echo "  Address the items above before declaring the host forensics-ready."
  exit 1
fi
echo "✓ Preflight passed — host is forensics-ready"
