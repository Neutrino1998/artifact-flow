#!/usr/bin/env bash
# Small, source-free config release workflow for an installed Fleet control host.

set -uo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CURRENT_LINK="$ROOT/.artifactflow/current"
HOTFIX_BUNDLE_DIR="${AF_HOTFIX_BUNDLE_DIR:-$ROOT/.artifactflow/hotfix-bundles}"
BASE_RELEASE_FILE=".artifactflow-base-release"
BASE_CONFIG_DIR=".artifactflow-base-config"
CLEANUP_DIR=""

die() { printf 'Error: %s\n' "$1" >&2; exit "${2:-1}"; }
cleanup() { [[ -z "$CLEANUP_DIR" ]] || rm -rf "$CLEANUP_DIR"; }
trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage:
  fleet.sh config checkout <workspace>
  fleet.sh config apply [--id ID] [--platform linux/amd64|linux/arm64]
                        [--maintenance] [--dry-run] <workspace>

checkout copies the active config plus a private baseline into an empty
workspace. Edit workspace/config, then apply packages a config-only release
and sends it through the normal Fleet reconcile, rolling restart, health and
activation path. No source checkout, git, Docker build, or release.sh needed.
EOF
}

active_release() {
  [[ -L "$CURRENT_LINK" && -f "$CURRENT_LINK/.af-release" && -d "$CURRENT_LINK/config" ]] \
    || die "no active immutable release; deploy one app release first"
  (cd "$CURRENT_LINK" && pwd -P)
}

release_id_from() {
  awk -F= '$1 == "release_id" {sub(/^[^=]*=/, ""); print; exit}' "$1/.af-release"
}

validate_release_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "invalid release id '$1' (allowed: letters, digits, dot, underscore, dash)"
}

default_platform() {
  local arch=""
  if [[ -f "${AF_FLEET_CONF:-$ROOT/deploy/fleet.conf}" ]]; then
    arch="$(awk '
      { sub(/#.*/, "") }
      $1 == "infra" || $1 == "release" || $1 == "app" || $1 == "lb" {
        for (i=3; i<=NF; i++) if ($i ~ /^arch=/) { sub(/^arch=/, "", $i); print $i; exit }
      }
    ' "${AF_FLEET_CONF:-$ROOT/deploy/fleet.conf}")"
  fi
  if [[ -z "$arch" ]]; then
    case "$(uname -m)" in
      arm64|aarch64) arch=arm64 ;;
      *) arch=amd64 ;;
    esac
  fi
  printf 'linux/%s\n' "$arch"
}

checked_diff_status() {  # checked_diff_status <left> <right>: 0=same, 1=different, >1=error
  diff -qr "$1" "$2" >/dev/null 2>&1
}

cmd_checkout() {
  local workspace="${1:-}" active base_id
  [[ -n "$workspace" && $# -eq 1 ]] || die "usage: fleet.sh config checkout <workspace>"
  [[ ! -e "$workspace" ]] || die "workspace already exists: $workspace"
  active="$(active_release)"
  base_id="$(release_id_from "$active")"
  [[ -n "$base_id" ]] || die "active release has no release_id metadata: $active"

  mkdir -p "$workspace" || die "cannot create workspace: $workspace"
  cp -a "$active/config" "$workspace/config" \
    || die "cannot copy active config into workspace"
  cp -a "$active/config" "$workspace/$BASE_CONFIG_DIR" \
    || die "cannot preserve config baseline"
  printf '%s\n' "$base_id" > "$workspace/$BASE_RELEASE_FILE"

  printf 'Config workspace ready: %s\n' "$workspace"
  printf 'Base release: %s\n' "$base_id"
  printf 'Edit %s/config, then run:\n' "$workspace"
  printf '  deploy/scripts/fleet.sh config apply --maintenance %q\n' "$workspace"
}

cmd_apply() {
  local release_id="" platform="" maintenance=0 dry=0 workspace=""
  local active active_id base_id base_config config bundle parent tmp diff_status
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --id)
        [[ $# -ge 2 ]] || die "--id needs a value"
        release_id="$2"; shift
        ;;
      --platform)
        [[ $# -ge 2 ]] || die "--platform needs a value"
        platform="$2"; shift
        ;;
      --maintenance) maintenance=1 ;;
      --dry-run) dry=1 ;;
      -*) die "unknown config apply flag: $1" ;;
      *) [[ -z "$workspace" ]] || die "config apply accepts one workspace"; workspace="$1" ;;
    esac
    shift
  done
  [[ -n "$workspace" ]] || die "usage: fleet.sh config apply [options] <workspace>"
  release_id="${release_id:-hotfix-config-$(date +%Y%m%d-%H%M%S)}"
  validate_release_id "$release_id"
  platform="${platform:-$(default_platform)}"
  case "$platform" in linux/amd64|linux/arm64) ;; *) die "unsupported platform: $platform" ;; esac

  active="$(active_release)"
  active_id="$(release_id_from "$active")"
  [[ -f "$workspace/$BASE_RELEASE_FILE" ]] \
    || die "not a config checkout (missing $BASE_RELEASE_FILE): $workspace"
  base_id="$(head -n 1 "$workspace/$BASE_RELEASE_FILE")"
  [[ "$active_id" == "$base_id" ]] \
    || die "active release changed since checkout ($base_id -> $active_id); checkout again before applying"
  base_config="$workspace/$BASE_CONFIG_DIR"
  config="$workspace/config"
  [[ -d "$base_config" && -d "$config" ]] \
    || die "config checkout is incomplete: $workspace"

  checked_diff_status "$active/config" "$base_config"; diff_status=$?
  (( diff_status <= 1 )) || die "cannot compare active config with checkout baseline"
  (( diff_status == 0 )) \
    || die "active config changed in place since checkout; checkout again before applying"
  checked_diff_status "$base_config" "$config"; diff_status=$?
  (( diff_status <= 1 )) || die "cannot compare edited config with checkout baseline"
  (( diff_status == 1 )) || die "config workspace has no changes"

  if (( dry )); then
    bundle="$(mktemp -d /tmp/artifactflow-config-hotfix.XXXXXX)" \
      || die "cannot create temporary hotfix bundle"
    CLEANUP_DIR="$bundle"
  else
    parent="$HOTFIX_BUNDLE_DIR"
    bundle="$parent/$release_id"
    [[ ! -e "$bundle" ]] \
      || die "hotfix bundle already exists: $bundle (rerun it directly with fleet.sh deploy-config)"
    mkdir -p "$parent" || die "cannot create hotfix bundle root: $parent"
    tmp="$(mktemp -d "$parent/.${release_id}.tmp.XXXXXX")" \
      || die "cannot create hotfix bundle staging directory"
    bundle="$tmp"
    CLEANUP_DIR="$tmp"
  fi

  tar -czf "$bundle/artifactflow-config-${release_id}.tar.gz" -C "$workspace" config \
    || die "cannot package edited config"
  (
    cd "$bundle" || exit 1
    sha256sum "artifactflow-config-${release_id}.tar.gz" \
      > "artifactflow-config-${release_id}.tar.gz.sha256"
  ) || die "cannot checksum config bundle"
  {
    printf 'ArtifactFlow Release %s\n' "$release_id"
    printf 'Release kind: config\n'
    printf 'Built:        %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'Built from:   active-release@%s\n' "$base_id"
    printf 'Platform:     %s\n' "$platform"
    printf 'Layout:       config\n'
  } > "$bundle/artifactflow-${release_id}.manifest.txt"

  if (( ! dry )); then
    mv "$bundle" "$parent/$release_id" || die "cannot finalize hotfix bundle"
    bundle="$parent/$release_id"
    CLEANUP_DIR=""
  fi

  printf 'Applying config hotfix %s from base %s\n' "$release_id" "$base_id"
  local fleet_args=(deploy-config)
  (( maintenance )) && fleet_args+=(--maintenance)
  (( dry )) && fleet_args+=(--dry-run)
  fleet_args+=("$bundle")
  if ! AF_BUNDLE_VERSION="$release_id" "$SCRIPT_DIR/fleet.sh" "${fleet_args[@]}"; then
    (( dry )) && die "Fleet config dry-run failed"
    die "Fleet config deploy failed; bundle retained at $bundle"
  fi
  (( dry )) || printf 'Hotfix bundle retained: %s\n' "$bundle"
}

case "${1:-}" in
  checkout) shift; cmd_checkout "$@" ;;
  apply) shift; cmd_apply "$@" ;;
  ""|-h|--help|help) usage ;;
  *) die "unknown config action: $1 (use checkout or apply)" ;;
esac
