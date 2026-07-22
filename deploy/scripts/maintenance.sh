#!/usr/bin/env bash
set -euo pipefail

ROOT="${AF_ROOT:-/opt/artifactflow}"
[[ -x "$ROOT/bin/afctl" ]] || { echo "missing $ROOT/bin/afctl" >&2; exit 1; }
exec "$ROOT/bin/afctl" --root "$ROOT" maintenance "$@"
