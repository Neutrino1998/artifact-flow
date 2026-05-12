#!/usr/bin/env bash
# Verify sha256 of every artifactflow-*.tar.gz in a directory.
#
# release.sh writes each .sha256 with a *bare* filename (no path), which
# requires sha256sum to run from the directory holding the tar — easy to
# get wrong by hand (and we hit it: "No such file or directory" when running
# from the project dir against tars in ../tmp/). This script handles the
# cd dance once and reports per-tar OK / FAIL.
#
# Usage:
#   deploy/scripts/verify-bundle.sh [DIR]
#
# DIR defaults to ./tmp/ relative to the repo root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DIR="${1:-$ROOT/tmp}"
if [[ ! -d "$DIR" ]]; then
  echo "Error: directory not found: $DIR" >&2
  exit 2
fi

shopt -s nullglob
sha_files=("$DIR"/artifactflow-*.tar.gz.sha256)
if (( ${#sha_files[@]} == 0 )); then
  echo "No artifactflow-*.tar.gz.sha256 files found in $DIR" >&2
  exit 1
fi

fail=0
echo "→ Verifying ${#sha_files[@]} bundle(s) in $DIR"
for sha in "${sha_files[@]}"; do
  basename=$(basename "$sha")
  tar=${basename%.sha256}

  if [[ ! -f "$DIR/$tar" ]]; then
    printf '  ✗ %s — tar missing alongside .sha256\n' "$tar"
    fail=1
    continue
  fi

  # CRLF line endings (common after Windows-flavored scp/sftp) break
  # GNU sha256sum's parser with a confusing "no properly formatted lines"
  # error. Surface it specifically — the fix is one sed command.
  if grep -q $'\r' "$sha"; then
    printf '  ✗ %s — .sha256 has CRLF line endings\n' "$tar"
    echo "      fix: sed -i 's/\\r\$//' '$sha'"
    fail=1
    continue
  fi

  if output=$(cd "$DIR" && sha256sum -c "$basename" 2>&1); then
    printf '  ✓ %s\n' "$tar"
  else
    printf '  ✗ %s — hash mismatch or read error\n' "$tar"
    printf '%s\n' "$output" | sed 's/^/      /'
    fail=1
  fi
done

if (( fail )); then
  echo "✗ Verification failed — do NOT proceed with docker load / tar xzf until fixed"
  exit 1
fi
echo "✓ All bundles verified"
