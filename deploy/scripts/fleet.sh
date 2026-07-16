#!/usr/bin/env bash
# fleet.sh — ArtifactFlow unified deploy entrypoint (Phase D, 决策 7).
#
# One command surface and release model for the whole "single box →
# multi-worker → multi-host" continuum. Same-host dependency ordering is owned
# by Compose; cross-host ordering is owned by Fleet. They share bundle checks,
# immutable staging, activation/state and rollback, while SSH/firewall/static-
# upstream seams still require a physical multi-host acceptance run.
#
# Topology lives in deploy/fleet.conf (copy from fleet.conf.example). Roles:
# infra (pg+redis) / release (one-shot migrate+reconcile) / app (backend×scale
# + frontend) / lb (caddy).
#
# Subcommands:
#   fleet.sh init-local                write single-host fleet.conf + seed deploy/.env
#   fleet.sh bootstrap <bundle-dir>    init-local (if needed) → preflight → first deploy
#   fleet.sh preflight                 per-host docker/compose/disk/clock + deploy/.env checks
#   fleet.sh deploy <bundle-dir>       verify → extract → load → release gate → (rolling) up → LB → smoke
#   fleet.sh deploy --dry-run <dir>    print the plan, touch nothing
#   fleet.sh deploy-config <dir>       verify → stage config → reconcile → rolling app restart
#   fleet.sh config checkout|apply     edit/package/apply config on a Fleet control host
#   fleet.sh prepare-sandbox <dir>     load sandbox image; install runsc only when bundled
#   fleet.sh status                    per-host `compose ps` + /health/ready probe
#   fleet.sh rollback                  re-up the previously-deployed version (images kept)
#   fleet.sh rollback --dry-run        print the rollback plan
#   fleet.sh env check|apply [file]     validate/apply target-local environment
#   fleet.sh proxy-reload               reload Caddy config/cert on the LB host
#   fleet.sh maintenance on|off|status  fleet-aware maintenance flag
#
# Env (all optional):
#   AF_FLEET_CONF     topology file           (default deploy/fleet.conf)
#   AF_FLEET_STATE    version state file      (default deploy/.fleet-state)
#   AF_SSH_USER       ssh user for remote hosts (default: current user)
#   AF_SSH_OPTS       extra ssh/scp options   (e.g. "-i ~/.ssh/fleet -p 2222")
#   AF_REMOTE_DIR     install dir on remote hosts (default /opt/artifactflow)
#   AF_READY_TIMEOUT  seconds to wait for /health/ready green (default 120)
#   AF_HTTPS_PORT     LB HTTPS port for smoke (default 443)
#   AF_ENABLE_SANDBOX override deploy/.env's required 0/1 sandbox policy
#   AF_BUNDLE_VERSION select a version when bundle dir contains many manifests
#
# Exit: 0 = success; non-zero = a step failed (message on stderr, sequence stops).
#
# ── Single-host vs multi-host acceptance ──
# Single-host (all roles `local`) is the exercised path. Multi-host now has an
# executable transport/order path (published app overlay, generated static
# Caddy upstreams, per-host env merge, rolling app hosts), but still awaits its
# first physical two-machine acceptance run. Until per-replica port discovery
# exists, multi-host app rows are structurally limited to scale=1.

set -uo pipefail  # not -e: we drive the sequence with explicit die() so every
                  # stop point carries a diagnostic, and per-host loops report
                  # which host failed instead of a bare non-zero.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DIR="$ROOT/deploy"
RUNTIME_ROOT="$ROOT/.artifactflow"
RELEASES_DIR="$RUNTIME_ROOT/releases"
CURRENT_LINK="$RUNTIME_ROOT/current"
COMPOSE_BASENAME="docker-compose.intranet.yml"
SANDBOX_COMPOSE_BASENAME="docker-compose.sandbox.yml"
FLEET_APP_COMPOSE_BASENAME="docker-compose.fleet-app.yml"
ENABLE_SANDBOX=""

FLEET_CONF="${AF_FLEET_CONF:-$DEPLOY_DIR/fleet.conf}"
STATE_FILE="${AF_FLEET_STATE:-$DEPLOY_DIR/.fleet-state}"
SSH_USER="${AF_SSH_USER:-$(id -un)}"
SSH_OPTS="${AF_SSH_OPTS:-}"
REMOTE_DIR="${AF_REMOTE_DIR:-/opt/artifactflow}"
READY_TIMEOUT="${AF_READY_TIMEOUT:-120}"
HTTPS_PORT="${AF_HTTPS_PORT:-443}"

# ── output helpers ──────────────────────────────────────────────────
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }
info() { printf '  \033[2mℹ %s\033[0m\n' "$1"; }
step() { printf '\033[1m▶ %s\033[0m\n' "$1"; }
die()  { bad "$1"; exit "${2:-1}"; }

# ── fleet.conf parsing → parallel topology arrays ─────────────────
ROLE=(); HOST=(); ARCH=(); SCALE=(); ADVERTISE=()
parse_conf() {
  [[ -f "$FLEET_CONF" ]] || die "fleet.conf not found: $FLEET_CONF (copy from fleet.conf.example)"
  # Make repeated parses describe only the current file contents.
  ROLE=(); HOST=(); ARCH=(); SCALE=(); ADVERTISE=()
  local line role host rest kv a s d j
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"                       # strip inline comment
    read -r role host rest <<<"$line"        # split first two + remainder
    [[ -z "${role:-}" ]] && continue
    [[ -z "${host:-}" ]] && die "fleet.conf: role '$role' has no host"
    a=""; s=""; d=""
    for kv in $rest; do
      case "$kv" in
        arch=*)  a="${kv#arch=}" ;;
        scale=*) s="${kv#scale=}" ;;
        advertise=*) d="${kv#advertise=}" ;;
        *) die "fleet.conf: unknown field '$kv' on row '$role $host'" ;;
      esac
    done
    case "$role" in infra|release|app|lb) ;; *) die "fleet.conf: bad role '$role'";; esac
    [[ -z "$a" || "$a" == amd64 || "$a" == arm64 ]] \
      || die "fleet.conf: arch must be amd64 or arm64 (got '$a' on $role $host)"
    if [[ -n "$s" ]]; then
      [[ "$role" == app ]] || die "fleet.conf: scale is only valid on app rows ($role $host)"
      [[ "$s" =~ ^[0-9]+$ && "$s" -ge 1 ]] \
        || die "fleet.conf: scale must be a positive integer on app $host"
    fi
    if [[ -n "$d" ]]; then
      [[ "$role" == app ]] || die "fleet.conf: advertise is only valid on app rows ($role $host)"
      [[ "$d" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "fleet.conf: advertise must be a DNS name or IPv4 address on app $host"
    fi
    for j in "${!ROLE[@]}"; do
      [[ "${ROLE[$j]}" != "$role" || "${HOST[$j]}" != "$host" ]] \
        || die "fleet.conf: duplicate row '$role $host'"
      [[ -z "$a" || -z "${ARCH[$j]}" || "${HOST[$j]}" != "$host" || "${ARCH[$j]}" == "$a" ]] \
        || die "fleet.conf: host $host has conflicting arch values (${ARCH[$j]} vs $a)"
    done
    ROLE+=("$role"); HOST+=("$host"); ARCH+=("$a"); SCALE+=("$s"); ADVERTISE+=("$d")
  done < "$FLEET_CONF"
  [[ ${#ROLE[@]} -gt 0 ]] || die "fleet.conf is empty"
  # cardinality: exactly one release + one lb; at least one app
  local nr=0 nl=0 na=0 i
  for i in "${!ROLE[@]}"; do
    case "${ROLE[$i]}" in release) nr=$((nr+1));; lb) nl=$((nl+1));; app) na=$((na+1));; esac
  done
  (( nr == 1 )) || die "fleet.conf needs exactly one 'release' row (found $nr)"
  (( nl == 1 )) || die "fleet.conf needs exactly one 'lb' row (found $nl)"
  (( na >= 1 )) || die "fleet.conf needs at least one 'app' row (found $na)"
  if [[ "$(all_hosts)" != local ]]; then
    for i in $(app_indices); do
      [[ -n "${ADVERTISE[$i]}" ]] \
        || die "fleet.conf: multi-host app ${HOST[$i]} needs advertise=<LB-reachable-address>"
    done
  fi
}

role_host() {  # echo the (first) host for a role; empty if absent
  local want="$1" i
  for i in "${!ROLE[@]}"; do [[ "${ROLE[$i]}" == "$want" ]] && { echo "${HOST[$i]}"; return 0; }; done
  return 1
}
app_indices() { local i; for i in "${!ROLE[@]}"; do [[ "${ROLE[$i]}" == app ]] && echo "$i"; done; }
has_infra()   { role_host infra >/dev/null; }
host_has_role() {  # host_has_role <host> <role>
  local host="$1" want="$2" i
  for i in "${!ROLE[@]}"; do
    [[ "${HOST[$i]}" == "$host" && "${ROLE[$i]}" == "$want" ]] && return 0
  done
  return 1
}

host_arch() {
  local host="$1" i
  for i in "${!HOST[@]}"; do
    [[ "${HOST[$i]}" == "$host" && -n "${ARCH[$i]}" ]] \
      && { printf '%s\n' "${ARCH[$i]}"; return 0; }
  done
  return 1
}

# distinct host list across all roles (order-preserving)
all_hosts() {
  local i h seen=" "
  for i in "${!HOST[@]}"; do
    h="${HOST[$i]}"
    [[ "$seen" == *" $h "* ]] || { echo "$h"; seen="$seen$h "; }
  done
}
is_local() { [[ "$1" == local ]]; }
target_dir() { is_local "$1" && echo "$ROOT" || echo "$REMOTE_DIR"; }

# ── transport: local runs in-place, remote over ssh ─────────────────
run_on() {  # run_on <host> <command-string>
  local host="$1"; shift
  if is_local "$host"; then
    bash -c "$*"
  else
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$SSH_USER@$host" "$*"
  fi
}
copy_to() {  # copy_to <host> <local-src> <dst-path>
  local host="$1" src="$2" dst="$3"
  if is_local "$host"; then
    [[ "$src" == "$dst" ]] || cp -a "$src" "$dst"
  else
    # shellcheck disable=SC2086
    scp $SSH_OPTS -q "$src" "$SSH_USER@$host:$dst"
  fi
}

run_prepare_host_check() {  # run_prepare_host_check <host>
  local host="$1" dir with_infra=0 version_env="" sandbox_env="" release_env="" expected_version=""
  dir="$(target_dir "$host")"
  host_has_role "$host" infra && with_infra=1
  expected_version="${BUNDLE_VER:-${AF_BUNDLE_VERSION:-}}"
  [[ -n "$expected_version" ]] && version_env="AF_VERSION='$expected_version' "
  [[ -n "${BUNDLE_SANDBOX_IMAGE:-}" ]] && sandbox_env="AF_SANDBOX_IMAGE_REF='$BUNDLE_SANDBOX_IMAGE' "
  if [[ -n "${BUNDLE_VER:-}" ]]; then
    release_env="AF_RELEASE_ROOT='$(release_dir_for "$dir" "$BUNDLE_VER")' "
  elif [[ -n "$(state_get current)" ]]; then
    release_env="AF_RELEASE_ROOT='$(release_dir_for "$dir" "$(state_get current)")' "
  fi

  run_on "$host" "if [ -d '$dir' ]; then cd '$dir'; else echo '  ℹ prepare-host check skipped ($dir not created yet)'; exit 0; fi; if [ -x deploy/scripts/prepare-host.sh ]; then AF_WITH_INFRA='$with_infra' AF_ENABLE_SANDBOX='$ENABLE_SANDBOX' ${version_env}${sandbox_env}${release_env}deploy/scripts/prepare-host.sh check; else echo '  ℹ prepare-host check skipped (deploy/scripts/prepare-host.sh not found yet)'; fi"
}

env_file_value() {
  local env_file="$1" key="$2" line value
  [[ -f "$env_file" ]] || return 1
  line="$(grep -E "^${key}=" "$env_file" | tail -1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="${value%$'\r'}"
  printf '%s\n' "$value"
}

resolve_sandbox_enablement() {
  local value="" source="deploy/.env"
  if [[ ${AF_ENABLE_SANDBOX+x} ]]; then
    value="$AF_ENABLE_SANDBOX"
    source="AF_ENABLE_SANDBOX environment override"
  else
    value="$(env_file_value "$DEPLOY_DIR/.env" AF_ENABLE_SANDBOX || true)"
  fi

  case "$value" in
    0|1) ENABLE_SANDBOX="$value" ;;
    "")
      die "AF_ENABLE_SANDBOX is not configured; add AF_ENABLE_SANDBOX=0 or 1 to $DEPLOY_DIR/.env (use 1 for existing sandbox-enabled deployments)"
      ;;
    *) die "$source must be exactly 0 or 1 (got: $value)" ;;
  esac
}

secure_env_file() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  chmod 600 "$env_file" || die "failed to chmod 600 $env_file"
}

env_file_set() {
  local env_file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp "${env_file}.tmp.XXXXXX")" || die "failed to create temp env file"
  chmod 600 "$tmp" || die "failed to chmod 600 $tmp"
  if awk -v key="$key" -v value="$value" '
    BEGIN { done = 0 }
    $0 ~ "^" key "=" {
      print key "=" value
      done = 1
      next
    }
    { print }
    END {
      if (!done) print key "=" value
    }
  ' "$env_file" > "$tmp"; then
    mv "$tmp" "$env_file" || die "failed to update $env_file"
    secure_env_file "$env_file"
  else
    rm -f "$tmp"
    die "failed to update $env_file"
  fi
}

gen_urlsafe_secret() {
  local bytes="${1:-32}" keep_padding="${2:-0}"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$bytes" "$keep_padding" <<'PY'
import base64
import os
import secrets
import sys

size = int(sys.argv[1])
keep_padding = sys.argv[2] == "1"
if keep_padding:
    print(base64.urlsafe_b64encode(os.urandom(size)).decode())
else:
    print(secrets.token_urlsafe(size))
PY
  elif command -v openssl >/dev/null 2>&1; then
    if [[ "$keep_padding" == 1 ]]; then
      openssl rand -base64 "$bytes" | tr '+/' '-_' | tr -d '\n'
    else
      openssl rand -base64 "$bytes" | tr '+/' '-_' | tr -d '=\n'
    fi
  else
    die "cannot generate secrets: need python3 or openssl"
  fi
}

seed_env_generated_values() {
  local env_file="$1" jwt credential pg_password pg_user pg_db db_url generated=0
  [[ -f "$env_file" ]] || return 0
  secure_env_file "$env_file"

  jwt="$(env_file_value "$env_file" ARTIFACTFLOW_JWT_SECRET || true)"
  if [[ -z "$jwt" || "$jwt" == *CHANGE_ME* ]]; then
    env_file_set "$env_file" ARTIFACTFLOW_JWT_SECRET "$(gen_urlsafe_secret 32 0)"
    generated=$((generated + 1))
  fi

  credential="$(env_file_value "$env_file" ARTIFACTFLOW_CREDENTIAL_KEY || true)"
  if [[ -z "$credential" || "$credential" == *CHANGE_ME* ]]; then
    # Fernet key = urlsafe-base64 encoding of exactly 32 random bytes, padding kept.
    env_file_set "$env_file" ARTIFACTFLOW_CREDENTIAL_KEY "$(gen_urlsafe_secret 32 1)"
    generated=$((generated + 1))
  fi

  pg_password="$(env_file_value "$env_file" POSTGRES_PASSWORD || true)"
  if [[ -z "$pg_password" || "$pg_password" == *CHANGE_ME* ]]; then
    pg_password="$(gen_urlsafe_secret 24 0)"
    pg_user="$(env_file_value "$env_file" POSTGRES_USER || true)"
    pg_db="$(env_file_value "$env_file" POSTGRES_DB || true)"
    pg_user="${pg_user:-artifactflow}"
    pg_db="${pg_db:-artifactflow}"
    env_file_set "$env_file" POSTGRES_PASSWORD "$pg_password"
    db_url="$(env_file_value "$env_file" ARTIFACTFLOW_DATABASE_URL || true)"
    if [[ -z "$db_url" || "$db_url" == *CHANGE_ME* || "$db_url" == *@postgres:* ]]; then
      env_file_set "$env_file" ARTIFACTFLOW_DATABASE_URL "postgresql+asyncpg://${pg_user}:${pg_password}@postgres:5432/${pg_db}"
    fi
    generated=$((generated + 1))
  fi

  if (( generated > 0 )); then
    ok "generated first-run secrets in $env_file (existing files are never overwritten)"
  fi
  secure_env_file "$env_file"
}

sandbox_scratch_root_local() {
  local root=""
  root="$(env_file_value "$DEPLOY_DIR/.env" ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT || true)"
  root="${root:-${ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT:-/var/lib/artifactflow/sandbox-scratch}}"
  printf '%s\n' "$root"
}

sandbox_scratch_check_cmd() {
  local dir="$1"
  printf '%s' "env_file='$dir/deploy/.env'; root=\$(awk -F= '/^ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=/{v=\$2} END{print v}' \"\$env_file\" 2>/dev/null); root=\${root:-/var/lib/artifactflow/sandbox-scratch}; findmnt -rn \"\$root\" >/dev/null"
}

# ── immutable release roots + compose routing ──────────────────────
validate_release_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
    || die "invalid release id '$1' (allowed: letters, digits, dot, underscore, dash)"
}

release_dir_for() {  # release_dir_for <install-root> <release-id>
  printf '%s/.artifactflow/releases/%s\n' "$1" "$2"
}

active_release_dir_local() {
  if [[ -L "$CURRENT_LINK" && -f "$CURRENT_LINK/deploy/$COMPOSE_BASENAME" ]]; then
    (cd "$CURRENT_LINK" && pwd -P)
  else
    # Compatibility for deployments created before immutable release roots.
    printf '%s\n' "$ROOT"
  fi
}

release_meta_value_local() {  # release_meta_value_local <release-root> <key>
  local release_root="$1" key="$2"
  [[ -f "$release_root/.af-release" ]] || return 1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' \
    "$release_root/.af-release"
}

release_app_version_local() {
  local release_root="$1" value=""
  value="$(release_meta_value_local "$release_root" app_version || true)"
  if [[ -n "$value" ]]; then printf '%s\n' "$value"; else state_get current; fi
}

activate_release_local() {  # activate_release_local <release-root>
  local release_root="$1" link_tmp="$CURRENT_LINK.tmp.$$"
  mkdir -p "$RUNTIME_ROOT" || die "failed to create $RUNTIME_ROOT"
  ln -s "$release_root" "$link_tmp" || die "failed to create active-release link"
  # Target hosts are Linux; -T replaces the symlink itself instead of treating
  # a symlink-to-directory as a destination directory.
  mv -Tf "$link_tmp" "$CURRENT_LINK" || die "failed to activate release $release_root"
}

shell_args() {
  local arg
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
}

compose_on_release() {  # compose_on_release <host> <release-id> <app-version> <args...>
  local host="$1" release_id="$2" app_ver="$3"; shift 3
  local dir release_root flags args
  dir="$(target_dir "$host")"
  release_root="$(release_dir_for "$dir" "$release_id")"
  flags="--env-file '$dir/deploy/.env' -f '$release_root/deploy/$COMPOSE_BASENAME'"
  if [[ "$ENABLE_SANDBOX" == 1 ]]; then
    flags+=" -f '$release_root/deploy/$SANDBOX_COMPOSE_BASENAME'"
  fi
  if host_has_role "$host" app && [[ "$(all_hosts)" != local ]]; then
    flags+=" -f '$release_root/deploy/$FLEET_APP_COMPOSE_BASENAME'"
  fi
  args="$(shell_args "$@")"
  run_on "$host" "cd '$dir/deploy' && AF_RUNTIME_DEPLOY_DIR='$dir/deploy' AF_VERSION='$app_ver' docker compose $flags$args"
}

# ── bundle introspection (manifest is source of truth) ──────────────
BUNDLE=""; BUNDLE_MANIFEST=""; BUNDLE_VER=""; BUNDLE_KIND=""; BUNDLE_PLATFORM=""; BUNDLE_SANDBOX_IMAGE=""; BUNDLE_GVISOR_PACKAGE=""; APP_TAR=""
SANDBOX_TAR=""; SANDBOX_VERIFY_TAR=""; SANDBOX_GVISOR_TAR=""
is_release_manifest() {
  local header
  header="$(head -n 1 "$1" 2>/dev/null || true)"
  [[ "$header" == ArtifactFlow\ Release\ * ]]
}

manifest_value() {  # manifest_value <file> <key>
  local file="$1" key="$2"
  awk -v prefix="${key}:" '
    index($0, prefix) == 1 {
      value = substr($0, length(prefix) + 1)
      sub(/^[[:space:]]*/, "", value)
      print value
      exit
    }
  ' "$file"
}

load_bundle_meta() {
  BUNDLE="$1"
  [[ -d "$BUNDLE" ]] || die "bundle dir not found: $BUNDLE"
  local mf="" selected="${AF_BUNDLE_VERSION:-}" gvisor_arch=""
  if [[ -n "$selected" ]]; then
    mf="$BUNDLE/artifactflow-${selected}.manifest.txt"
    [[ -f "$mf" ]] || die "AF_BUNDLE_VERSION=$selected but manifest not found: $mf"
    is_release_manifest "$mf" || die "manifest is not an ArtifactFlow release manifest: $mf"
  else
    local manifests=()
    local line
    while IFS= read -r line; do
      is_release_manifest "$line" && manifests+=("$line")
    done < <(find "$BUNDLE" -maxdepth 1 -type f -name 'artifactflow-*.manifest.txt' ! -name 'artifactflow-sandbox-*.manifest.txt' -print | sort)
    [[ ${#manifests[@]} -gt 0 ]] || die "no artifactflow release manifest in $BUNDLE"
    if [[ ${#manifests[@]} -gt 1 ]]; then
      die "multiple manifests in $BUNDLE; set AF_BUNDLE_VERSION=<version> or deploy from a clean bundle directory"
    fi
    mf="${manifests[0]}"
  fi
  BUNDLE_VER="$(awk 'NR==1{print $NF}' "$mf")"
  BUNDLE_MANIFEST="$mf"
  BUNDLE_KIND="$(manifest_value "$mf" "Release kind")"
  BUNDLE_KIND="${BUNDLE_KIND:-app}"
  BUNDLE_PLATFORM="$(manifest_value "$mf" "Platform")"
  BUNDLE_SANDBOX_IMAGE="$(manifest_value "$mf" "Sandbox image required")"
  BUNDLE_GVISOR_PACKAGE="$(manifest_value "$mf" "gVisor host runtime")"
  [[ -n "$BUNDLE_VER" ]] || die "cannot read version from manifest $mf"
  validate_release_id "$BUNDLE_VER"
  case "$BUNDLE_KIND" in app|config) ;; *) die "manifest has invalid Release kind '$BUNDLE_KIND': $mf" ;; esac
  if [[ "$BUNDLE_KIND" == app ]]; then
    [[ "$BUNDLE_SANDBOX_IMAGE" =~ ^artifactflow-sandbox:[0-9a-f]{16}-(amd64|arm64)$ ]] \
      || die "manifest has invalid or missing immutable sandbox image reference: $mf"
  fi
  case "$BUNDLE_GVISOR_PACKAGE" in
    ""|none|skipped*) BUNDLE_GVISOR_PACKAGE="" ;;
    sandbox-gvisor-*.tar.gz)
      gvisor_arch="$(bundle_gvisor_arch)"
      [[ -n "$gvisor_arch" \
         && "$BUNDLE_GVISOR_PACKAGE" != */* \
         && "$BUNDLE_GVISOR_PACKAGE" =~ ^sandbox-gvisor-release-[0-9]{8}\.[0-9]+-${gvisor_arch}\.tar\.gz$ ]] \
        || die "manifest gVisor package does not match Platform $BUNDLE_PLATFORM: $BUNDLE_GVISOR_PACKAGE"
      ;;
    *) die "manifest has invalid gVisor host runtime entry: $mf" ;;
  esac
  # Select the app tar by the manifest version, NOT a glob head -1 — keeps the
  # loaded image locked to the AF_VERSION compose resolves at `up` even if a dir
  # ever holds two versions' tars (release.sh writes one version per dist/, so
  # this is defensive, but the coupling is free).
  APP_TAR=""
  if [[ "$BUNDLE_KIND" == app ]]; then
    APP_TAR="$BUNDLE/artifactflow-app-${BUNDLE_VER}.tar.gz"
    [[ -f "$APP_TAR" ]] || die "app tar for version $BUNDLE_VER not found: $APP_TAR"
  fi
}

# Normalize any arch spelling to a canonical family token, so a compose
# Platform (linux/arm64), a Linux `uname -m` (aarch64) and a Darwin one (arm64)
# all compare equal. Same for amd64/x86_64.
canon_arch() {
  case "$1" in
    */amd64|amd64|x86_64|x64) echo x86_64 ;;
    */arm64|*/aarch64|arm64|aarch64) echo arm64 ;;
    *) echo "$1" ;;
  esac
}

bundle_image_arch() {
  case "$(canon_arch "$BUNDLE_PLATFORM")" in
    x86_64) echo amd64 ;;
    arm64)  echo arm64 ;;
    *)      echo "" ;;
  esac
}

bundle_gvisor_arch() {
  case "$(canon_arch "$BUNDLE_PLATFORM")" in
    x86_64) echo x86_64 ;;
    arm64)  echo aarch64 ;;
    *)      echo "" ;;
  esac
}

load_bundle_sandbox_meta() {
  local image_arch
  image_arch="$(bundle_image_arch)"
  SANDBOX_TAR=""
  SANDBOX_VERIFY_TAR=""
  SANDBOX_GVISOR_TAR=""
  if [[ -n "$image_arch" ]]; then
    SANDBOX_TAR="$BUNDLE/artifactflow-sandbox-${BUNDLE_VER}-${image_arch}.tar.gz"
  fi
  SANDBOX_VERIFY_TAR="$BUNDLE/artifactflow-sandbox-verify-${BUNDLE_VER}.tar.gz"
  if [[ -n "$BUNDLE_GVISOR_PACKAGE" ]]; then
    SANDBOX_GVISOR_TAR="$BUNDLE/$BUNDLE_GVISOR_PACKAGE"
    [[ -f "$SANDBOX_GVISOR_TAR" ]] \
      || die "manifest-declared gVisor package not found: $SANDBOX_GVISOR_TAR"
  fi
}

STAGED_RELEASE=""

release_identity_digest() {  # release_identity_digest <lineage> <unit-file>...
  local lineage="$1" file; shift
  {
    printf 'lineage|%s\n' "$lineage"
    for file in "$@"; do
      printf '%s|%s\n' "$(basename "$file")" "$(sha256sum "$file" | awk '{print $1}')"
    done
  } | sha256sum | awk '{print $1}'
}

stage_app_release_local() {
  local config_tar deploy_tar release_root tmp digest recorded=""
  config_tar="$BUNDLE/artifactflow-config-${BUNDLE_VER}.tar.gz"
  deploy_tar="$BUNDLE/artifactflow-deploy-${BUNDLE_VER}.tar.gz"
  [[ -f "$config_tar" ]] || die "config tar for version $BUNDLE_VER not found: $config_tar"
  [[ -f "$deploy_tar" ]] || die "deploy tar for version $BUNDLE_VER not found: $deploy_tar"
  release_root="$(release_dir_for "$ROOT" "$BUNDLE_VER")"

  step "stage immutable release $BUNDLE_VER"
  if (( DRY )); then
    info "would extract deploy/config into $release_root (current remains untouched)"
    STAGED_RELEASE="$release_root"
    return 0
  fi

  digest="$(release_identity_digest app "$APP_TAR" "$deploy_tar" "$config_tar")"
  if [[ -d "$release_root" ]]; then
    recorded="$(release_meta_value_local "$release_root" bundle_digest || true)"
    [[ "$recorded" == "$digest" ]] \
      || die "release id $BUNDLE_VER already exists with different content: $release_root"
    STAGED_RELEASE="$release_root"
    ok "release already staged with matching digest"
    return 0
  fi

  mkdir -p "$RELEASES_DIR" || die "failed to create $RELEASES_DIR"
  tmp="$(mktemp -d "$RELEASES_DIR/.${BUNDLE_VER}.tmp.XXXXXX")" \
    || die "failed to create release staging directory"
  tar xzf "$deploy_tar" -C "$tmp" \
    || { rm -rf "$tmp"; die "deploy tar extract failed"; }
  tar xzf "$config_tar" -C "$tmp" \
    || { rm -rf "$tmp"; die "config tar extract failed"; }
  [[ -f "$tmp/deploy/$COMPOSE_BASENAME" && -d "$tmp/config" ]] \
    || { rm -rf "$tmp"; die "release tar layout invalid (need deploy/$COMPOSE_BASENAME + config/)"; }
  grep -q 'AF_RUNTIME_DEPLOY_DIR' "$tmp/deploy/$COMPOSE_BASENAME" \
    || { rm -rf "$tmp"; die "bundle deploy compose predates immutable release support; rebuild with the current release.sh"; }
  {
    printf 'release_id=%s\n' "$BUNDLE_VER"
    printf 'kind=app\n'
    printf 'app_version=%s\n' "$BUNDLE_VER"
    printf 'bundle_digest=%s\n' "$digest"
  } > "$tmp/.af-release"
  mv "$tmp" "$release_root" || { rm -rf "$tmp"; die "failed to finalize $release_root"; }
  STAGED_RELEASE="$release_root"
  ok "staged $release_root"
}

stage_config_release_local() {
  local config_tar release_root tmp digest recorded="" active active_id app_ver
  config_tar="$BUNDLE/artifactflow-config-${BUNDLE_VER}.tar.gz"
  [[ -f "$config_tar" ]] || die "config tar for version $BUNDLE_VER not found: $config_tar"
  release_root="$(release_dir_for "$ROOT" "$BUNDLE_VER")"
  active="$(active_release_dir_local)"
  [[ "$active" != "$ROOT" && -f "$active/.af-release" ]] \
    || die "deploy-config requires one successful immutable app release first"
  active_id="$(release_meta_value_local "$active" release_id || true)"
  [[ -n "$active_id" ]] || die "cannot determine active base release id"
  app_ver="$(release_app_version_local "$active")"
  [[ -n "$app_ver" ]] || die "cannot determine active app version"

  step "stage immutable config release $BUNDLE_VER (app stays $app_ver)"
  if (( DRY )); then
    info "would clone active deploy unit + extract config into $release_root"
    STAGED_RELEASE="$release_root"
    return 0
  fi
  digest="$(release_identity_digest "base:$active_id" "$config_tar")"
  if [[ -d "$release_root" ]]; then
    recorded="$(release_meta_value_local "$release_root" bundle_digest || true)"
    [[ "$recorded" == "$digest" ]] \
      || die "release id $BUNDLE_VER already exists with different content: $release_root"
    STAGED_RELEASE="$release_root"
    ok "config release already staged with matching digest"
    return 0
  fi
  mkdir -p "$RELEASES_DIR" || die "failed to create $RELEASES_DIR"
  tmp="$(mktemp -d "$RELEASES_DIR/.${BUNDLE_VER}.tmp.XXXXXX")" \
    || die "failed to create config staging directory"
  cp -a "$active/deploy" "$tmp/deploy" \
    || { rm -rf "$tmp"; die "failed to inherit active deploy unit"; }
  tar xzf "$config_tar" -C "$tmp" \
    || { rm -rf "$tmp"; die "config tar extract failed"; }
  [[ -d "$tmp/config" ]] || { rm -rf "$tmp"; die "config tar layout invalid"; }
  {
    printf 'release_id=%s\n' "$BUNDLE_VER"
    printf 'kind=config\n'
    printf 'app_version=%s\n' "$app_ver"
    printf 'bundle_digest=%s\n' "$digest"
  } > "$tmp/.af-release"
  mv "$tmp" "$release_root" || { rm -rf "$tmp"; die "failed to finalize $release_root"; }
  STAGED_RELEASE="$release_root"
  ok "staged $release_root"
}

prepare_sandbox_single_local() {
  local explicit="${1:-0}"
  [[ "$ENABLE_SANDBOX" == 1 || "$explicit" == 1 ]] || return 0
  load_bundle_sandbox_meta

  local has_image=0 has_verify=0 has_gvisor=0
  [[ -n "$SANDBOX_TAR" && -f "$SANDBOX_TAR" ]] && has_image=1
  [[ -n "$SANDBOX_VERIFY_TAR" && -f "$SANDBOX_VERIFY_TAR" ]] && has_verify=1
  [[ -n "$SANDBOX_GVISOR_TAR" && -f "$SANDBOX_GVISOR_TAR" ]] && has_gvisor=1

  if (( ! has_image && ! has_verify && ! has_gvisor )); then
    if (( explicit )); then
      die "sandbox transfer units not found in $BUNDLE"
    fi
    info "no sandbox transfer units in bundle — assuming host sandbox prerequisites are already prepared"
    return 0
  fi
  (( has_image == has_verify )) \
    || die "incomplete sandbox bundle in $BUNDLE (sandbox image + verify tars must be paired; gVisor is optional)"

  step "prepare sandbox host prerequisites"
  if (( DRY )); then
    if (( has_gvisor )); then
      info "gVisor package present — would install/update runsc"
    else
      info "gVisor package absent — would require existing runsc registration"
    fi
    info "would: AF_SANDBOX_IMAGE=$SANDBOX_TAR AF_SANDBOX_IMAGE_REF=$BUNDLE_SANDBOX_IMAGE AF_SANDBOX_VERIFY=$SANDBOX_VERIFY_TAR AF_GVISOR_PACKAGE=$SANDBOX_GVISOR_TAR deploy/scripts/prepare-host.sh sandbox"
  else
    AF_SANDBOX_IMAGE="$SANDBOX_TAR" \
    AF_SANDBOX_IMAGE_REF="$BUNDLE_SANDBOX_IMAGE" \
    AF_SANDBOX_VERIFY="$SANDBOX_VERIFY_TAR" \
    AF_GVISOR_PACKAGE="$SANDBOX_GVISOR_TAR" \
      "$SCRIPT_DIR/prepare-host.sh" sandbox || die "sandbox preparation failed"
  fi
}

sandbox_bundle_has_any_unit() {
  load_bundle_sandbox_meta
  [[ -n "$SANDBOX_TAR" && -f "$SANDBOX_TAR" ]] && return 0
  [[ -n "$SANDBOX_VERIFY_TAR" && -f "$SANDBOX_VERIFY_TAR" ]] && return 0
  [[ -n "$SANDBOX_GVISOR_TAR" && -f "$SANDBOX_GVISOR_TAR" ]] && return 0
  return 1
}

sandbox_ready_local() {
  command -v runsc >/dev/null 2>&1 || return 1
  docker info 2>/dev/null | grep -q runsc || return 1
  [[ -n "$BUNDLE_SANDBOX_IMAGE" ]] || return 1
  docker image inspect "$BUNDLE_SANDBOX_IMAGE" >/dev/null 2>&1 || return 1
  local scratch
  scratch="$(sandbox_scratch_root_local)"
  findmnt -rn "$scratch" >/dev/null || return 1
}

assert_app_sandbox_image_local() {
  local actual
  actual="$(
    docker image inspect "artifactflow:$BUNDLE_VER" \
      --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
      | awk -F= '$1 == "ARTIFACTFLOW_SANDBOX_IMAGE" {sub(/^[^=]*=/, ""); print; exit}'
  )"
  [[ "$actual" == "$BUNDLE_SANDBOX_IMAGE" ]] \
    || die "backend image sandbox reference mismatch: manifest=$BUNDLE_SANDBOX_IMAGE image=${actual:-<missing>}"
}

require_sandbox_ready_single_local() {
  [[ "$ENABLE_SANDBOX" == 1 ]] || return 0
  if (( DRY )); then
    info "would require sandbox host prerequisites; run prepare-sandbox first when using a sandbox bundle"
    return 0
  fi
  if sandbox_ready_local; then
    ok "sandbox host prerequisites already prepared"
    return 0
  fi
  if sandbox_bundle_has_any_unit; then
    die "AF_ENABLE_SANDBOX=1 but sandbox host prerequisites are not ready; run first as root: sudo env AF_BUNDLE_VERSION='$BUNDLE_VER' deploy/scripts/fleet.sh prepare-sandbox '$BUNDLE'"
  fi
  die "AF_ENABLE_SANDBOX=1 but sandbox host prerequisites are not ready and no sandbox transfer units were found in $BUNDLE; rerun release with --with-sandbox or prepare the host manually"
}

# assert a host's CPU arch matches the bundle Platform + the conf arch column.
# Under --dry-run a mismatch is informational (you may be planning on a Mac for
# an amd64/arm64 target); a real deploy loud-fails.
assert_arch() {  # assert_arch <host> <conf-arch>
  local host="$1" conf_arch="$2"
  local afail; afail() { (( DRY )) && info "$1" || die "$1"; }
  local want; want="$(canon_arch "$BUNDLE_PLATFORM")"
  local raw; raw="$(run_on "$host" 'uname -m' 2>/dev/null | tr -d '[:space:]')"
  [[ -n "$raw" ]] || { afail "$host: cannot read uname -m"; return 0; }
  local got; got="$(canon_arch "$raw")"
  if [[ "$got" != "$want" ]]; then
    afail "$host: arch mismatch — host is '$raw' ($got) but bundle Platform is '$BUNDLE_PLATFORM' ($want). release.sh builds one arch per run; rebuild with PLATFORM=linux/${conf_arch:-arm64}."
  else
    ok "arch $raw matches bundle ($want)"
  fi
  if [[ -n "$conf_arch" ]]; then
    [[ "$(canon_arch "$conf_arch")" == "$got" ]] || afail "$host: fleet.conf says arch=$conf_arch but host reports '$raw'"
  fi
}

# ── version state (for rollback) ────────────────────────────────────
state_get() { [[ -f "$STATE_FILE" ]] && awk -F= -v k="$1" '$1==k{print $2}' "$STATE_FILE"; }
state_write() {  # state_write <current> <previous>
  # Atomic: write a temp then rename, so a crash never leaves a half-written /
  # empty .fleet-state that would strand `rollback` with no `previous`.
  printf 'current=%s\nprevious=%s\n' "$1" "$2" > "$STATE_FILE.tmp" \
    && mv -f "$STATE_FILE.tmp" "$STATE_FILE"
}

# ── readiness + smoke through the LB ────────────────────────────────
lb_ready_cmd() {  # lb_ready_cmd <lb-host>: curl iff /health/ready is green
  local host="${1:-local}" port=""
  if declare -F host_env_value >/dev/null 2>&1; then
    port="$(host_env_value "$host" AF_HTTPS_PORT)"
  else
    port="$(env_file_value "$DEPLOY_DIR/.env" AF_HTTPS_PORT || true)"
  fi
  port="${port:-$HTTPS_PORT}"
  # self-signed intranet cert → -k; loopback on the lb host's published HTTPS port
  echo "curl -fsk --max-time 5 https://localhost:${port}/health/ready >/dev/null"
}
wait_ready_result() {  # wait_ready_result <lb-host>; returns non-zero, never exits
  local host="$1" waited=0 cmd; cmd="$(lb_ready_cmd "$host")"
  step "wait for /health/ready via LB ($host, timeout ${READY_TIMEOUT}s)"
  while (( waited < READY_TIMEOUT )); do
    if run_on "$host" "$cmd" 2>/dev/null; then ok "LB healthy after ${waited}s"; return 0; fi
    sleep 3; waited=$((waited+3))
  done
  bad "LB /health/ready not green within ${READY_TIMEOUT}s on $host"
  return 1
}
wait_ready() {  # wait_ready <lb-host>
  wait_ready_result "$1" || die "deployment did not become ready"
}
smoke() {  # smoke <lb-host>
  local host="$1" cmd; cmd="$(lb_ready_cmd "$host")"
  step "smoke: /health/ready through LB ($host)"
  run_on "$host" "$cmd" 2>/dev/null && ok "smoke passed" || die "smoke failed on $host"
}

# ════════════════════════════════════════════════════════════════════
# init-local
# ════════════════════════════════════════════════════════════════════
local_fleet_arch() {
  local raw canon
  raw="$(uname -m 2>/dev/null || true)"
  canon="$(canon_arch "$raw")"
  case "$canon" in
    x86_64) echo amd64 ;;
    arm64)  echo arm64 ;;
    *)      echo "${raw:-amd64}" ;;
  esac
}

cmd_init_local() {
  local force=0 scale=1 arch=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force) force=1 ;;
      --scale)
        shift
        [[ $# -gt 0 ]] || die "--scale requires a value"
        scale="$1"
        ;;
      --scale=*) scale="${1#--scale=}" ;;
      --arch)
        shift
        [[ $# -gt 0 ]] || die "--arch requires amd64 or arm64"
        arch="$1"
        ;;
      --arch=*) arch="${1#--arch=}" ;;
      -*) die "unknown init-local flag: $1" ;;
      *) die "unexpected init-local argument: $1" ;;
    esac
    shift
  done
  [[ "$scale" =~ ^[0-9]+$ && "$scale" -ge 1 ]] || die "--scale must be a positive integer"
  arch="${arch:-$(local_fleet_arch)}"
  case "$arch" in amd64|arm64) ;; *) die "--arch must be amd64 or arm64 (got: $arch)" ;; esac

  step "initialize local fleet files"
  if [[ -f "$FLEET_CONF" && $force != 1 ]]; then
    info "$FLEET_CONF already exists; pass --force to rewrite it"
  else
    mkdir -p "$(dirname "$FLEET_CONF")"
    cat > "$FLEET_CONF" <<EOF
infra    local
release  local
app      local   arch=${arch}  scale=${scale}
lb       local
EOF
    ok "wrote $FLEET_CONF"
  fi

  if [[ -f "$DEPLOY_DIR/.env" ]]; then
    secure_env_file "$DEPLOY_DIR/.env"
    info "$DEPLOY_DIR/.env already exists"
    if ! env_file_value "$DEPLOY_DIR/.env" AF_ENABLE_SANDBOX >/dev/null; then
      info "AF_ENABLE_SANDBOX is missing; add =1 for a sandbox deployment or =0 otherwise before preflight/deploy"
    fi
  elif [[ -f "$DEPLOY_DIR/.env.intranet.example" ]]; then
    cp "$DEPLOY_DIR/.env.intranet.example" "$DEPLOY_DIR/.env" || die "failed to seed deploy/.env"
    secure_env_file "$DEPLOY_DIR/.env"
    ok "seeded $DEPLOY_DIR/.env from .env.intranet.example"
    seed_env_generated_values "$DEPLOY_DIR/.env"
  else
    info "$DEPLOY_DIR/.env.intranet.example not found; create $DEPLOY_DIR/.env manually"
  fi
}

cmd_bootstrap() {
  local bundle="" scale=2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --scale)
        shift; [[ $# -gt 0 ]] || die "--scale requires a value"; scale="$1" ;;
      --scale=*) scale="${1#--scale=}" ;;
      -*) die "unknown bootstrap flag: $1" ;;
      *) bundle="$1" ;;
    esac
    shift
  done
  [[ -n "$bundle" ]] || die "usage: fleet.sh bootstrap [--scale N] <bundle-dir>"
  if [[ ! -f "$FLEET_CONF" ]]; then
    cmd_init_local --scale "$scale"
  else
    info "using existing topology $FLEET_CONF"
  fi
  # The bundle-aware dry run validates topology/manifest/arch and prints the
  # exact first-deploy plan. The real deploy repeats checksum and host checks
  # after staging the immutable release units.
  # Each CLI command gets a fresh process. Besides fixing topology arrays, this
  # prevents any future subcommand-global state from leaking from plan → apply.
  "$SCRIPT_DIR/fleet.sh" deploy --dry-run "$bundle" \
    || die "bootstrap plan failed"
  "$SCRIPT_DIR/fleet.sh" deploy "$bundle" \
    || die "bootstrap deploy failed"
}

# ════════════════════════════════════════════════════════════════════
# preflight
# ════════════════════════════════════════════════════════════════════
cmd_preflight() {
  resolve_sandbox_enablement
  parse_conf
  local fail=0 host
  step "preflight across $(all_hosts | wc -l | tr -d ' ') host(s)"
  while IFS= read -r host; do
    printf '\n  \033[1m[%s]\033[0m\n' "$host"
    run_on "$host" 'command -v docker >/dev/null'       && ok "docker present"           || { bad "docker missing"; fail=1; }
    run_on "$host" 'docker compose version >/dev/null 2>&1' && ok "docker compose v2"     || { bad "docker compose v2 missing"; fail=1; }
    run_on "$host" 'docker info >/dev/null 2>&1'         && ok "docker daemon reachable"  || { bad "docker daemon down / no perms"; fail=1; }
    # disk: warn under 5 GiB free on the install dir's filesystem. On a fresh
    # host the install dir doesn't exist yet, so `df $dir` would measure nothing
    # and (df fails, awk sees no NR==2 row → exits 0) FALSELY report OK — the
    # transport shells don't inherit our pipefail. Walk up to the nearest
    # existing ancestor and df THAT filesystem (the one that will hold $dir).
    local dir; dir="$(target_dir "$host")"
    run_on "$host" "d='$dir'; while [ ! -e \"\$d\" ] && [ \"\$d\" != / ]; do d=\$(dirname \"\$d\"); done; df -Pk \"\$d\" 2>/dev/null | awk 'NR==2{exit (\$4<5242880)} END{if(NR<2)exit 1}'" \
      && ok "disk ≥5GiB free for $dir" || { bad "low disk (<5GiB) or unreadable for $dir"; fail=1; }
    # clock: skew vs control host (informational — NTP is the real fix)
    if ! is_local "$host"; then
      local rt lt; rt="$(run_on "$host" 'date -u +%s' 2>/dev/null)"; lt="$(date -u +%s)"
      if [[ -n "$rt" ]]; then
        local skew=$(( rt>lt ? rt-lt : lt-rt ))
        (( skew <= 5 )) && ok "clock skew ${skew}s" || info "clock skew ${skew}s vs control (check NTP)"
      fi
    fi
    # runsc only needed where app (sandbox) runs
    local i is_app=0; for i in $(app_indices); do [[ "${HOST[$i]}" == "$host" ]] && is_app=1; done
    if (( is_app )); then
      if [[ "$ENABLE_SANDBOX" == 1 ]]; then
        run_on "$host" 'command -v runsc >/dev/null 2>&1' && ok "runsc present" \
          || { bad "runsc missing but AF_ENABLE_SANDBOX=1"; fail=1; }
        run_on "$host" 'docker info 2>/dev/null | grep -q runsc' && ok "docker runtime runsc registered" \
          || { bad "docker runtime runsc not registered but AF_ENABLE_SANDBOX=1"; fail=1; }
        if [[ -n "${AF_SANDBOX_IMAGE_REF:-}" ]]; then
          run_on "$host" "docker image inspect '$AF_SANDBOX_IMAGE_REF' >/dev/null 2>&1" \
            && ok "$AF_SANDBOX_IMAGE_REF loaded" \
            || { bad "$AF_SANDBOX_IMAGE_REF missing but AF_ENABLE_SANDBOX=1"; fail=1; }
        else
          info "sandbox image identity is checked against the release manifest during deploy"
        fi
        local scratch_cmd; scratch_cmd="$(sandbox_scratch_check_cmd "$(target_dir "$host")")"
        run_on "$host" "$scratch_cmd" \
          && ok "sandbox scratch root mounted" \
          || { bad "sandbox scratch root not mounted but AF_ENABLE_SANDBOX=1"; fail=1; }
      else
        run_on "$host" 'command -v runsc >/dev/null 2>&1' && ok "runsc present" \
          || info "runsc not found (OK unless AF_ENABLE_SANDBOX=1)"
      fi
    fi
    step "deployment config check ($host)"
    run_prepare_host_check "$host" && ok "prepare-host check passed" \
      || { bad "prepare-host check failed"; fail=1; }
  done < <(all_hosts)
  echo
  (( fail == 0 )) && ok "preflight OK" || die "preflight found blockers"
}

# ════════════════════════════════════════════════════════════════════
# deploy
# ════════════════════════════════════════════════════════════════════
DRY=0
MAINTENANCE_WINDOW=0

fleet_maintenance() {  # fleet_maintenance on|off|status [note]
  local action="$1" note="${2:-}" lb dir args
  lb="$(role_host lb)"; dir="$(target_dir "$lb")"
  args="$(shell_args "$action")$(shell_args "$note")"
  run_on "$lb" "cd '$dir' && deploy/scripts/maintenance.sh$args"
}

cmd_deploy() {
  local bundle=""
  DRY=0
  MAINTENANCE_WINDOW=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY=1 ;;
      --maintenance) MAINTENANCE_WINDOW=1 ;;
      -*) die "unknown deploy flag: $1" ;;
      *) bundle="$1" ;;
    esac
    shift
  done
  [[ -n "$bundle" ]] || die "usage: fleet.sh deploy [--dry-run] <bundle-dir>"
  resolve_sandbox_enablement
  parse_conf
  load_bundle_meta "$bundle"
  [[ "$BUNDLE_KIND" == app ]] \
    || die "bundle $BUNDLE_VER is config-only; use: fleet.sh deploy-config $bundle"
  (( DRY )) && info "DRY-RUN — no host will be touched"
  step "deploy version=$BUNDLE_VER platform=$BUNDLE_PLATFORM from $BUNDLE"

  # verify checksums up front (verify-bundle.sh handles the cd dance)
  if (( ! DRY )); then
    step "verify bundle checksums"
    "$SCRIPT_DIR/verify-bundle.sh" "$BUNDLE" || die "bundle checksum verification failed"
  else
    info "would run verify-bundle.sh $BUNDLE"
  fi
  if (( MAINTENANCE_WINDOW && ! DRY )); then
    fleet_maintenance on "Deploying release $BUNDLE_VER" \
      || die "failed to enable maintenance mode"
  fi

  local hosts; hosts="$(all_hosts)"
  local single=0
  [[ "$hosts" == "local" ]] && single=1

  if (( single )); then
    deploy_single_local
  else
    deploy_multi_host
  fi

  local prev; prev="$(state_get current)"
  echo
  if (( DRY )); then
    info "dry-run complete — plan above, nothing changed"
  else
    state_write "$BUNDLE_VER" "${prev:-}" \
      || die "release is healthy but failed to record Fleet state in $STATE_FILE"
    if (( MAINTENANCE_WINDOW )); then
      fleet_maintenance off \
        || die "release is healthy but maintenance mode could not be disabled"
    fi
    ok "deploy done — version $BUNDLE_VER live${prev:+, previous was $prev}"
  fi
}

restore_config_single_local() {
  local release_id="$1" release_root app_ver n
  [[ -n "$release_id" ]] || return 1
  release_root="$(release_dir_for "$ROOT" "$release_id")"
  [[ -f "$release_root/.af-release" ]] || return 1
  app_ver="$(release_app_version_local "$release_root")"
  n="$(single_app_scale)"
  info "restoring config/reconcile from $release_id"
  compose_on_release local "$release_id" "$app_ver" run --rm --no-deps release \
    || return 1
  compose_on_release local "$release_id" "$app_ver" up -d --no-deps --force-recreate \
    --scale "backend=$n" backend frontend || return 1
  wait_ready_result local
}

deploy_config_single_local() {
  local previous_release active app_ver n up_ok=0
  previous_release="$(state_get current)"
  active="$(active_release_dir_local)"
  stage_config_release_local
  app_ver="$(release_app_version_local "$STAGED_RELEASE")"
  n="$(single_app_scale)"

  if (( DRY )); then
    info "would run release/reconcile against staged config with app image $app_ver"
    info "would recreate backend/frontend from config release $BUNDLE_VER, then probe LB"
    return 0
  fi

  step "validate + reconcile staged config once"
  compose_on_release local "$BUNDLE_VER" "$app_ver" run --rm --no-deps release \
    || die "config release/reconcile gate failed; active release remains $previous_release"

  step "activate config on application services"
  compose_on_release local "$BUNDLE_VER" "$app_ver" up -d --no-deps --force-recreate \
    --scale "backend=$n" backend frontend && up_ok=1
  if (( ! up_ok )); then
    restore_config_single_local "$previous_release" \
      || bad "automatic config restore failed; maintenance mode should remain enabled"
    die "config service recreation failed"
  fi
  if ! wait_ready_result local; then
    restore_config_single_local "$previous_release" \
      || bad "automatic config restore failed; maintenance mode should remain enabled"
    die "config release failed readiness"
  fi
  smoke local
  activate_release_local "$STAGED_RELEASE"
}

cmd_deploy_config() {
  local bundle=""
  DRY=0
  MAINTENANCE_WINDOW=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY=1 ;;
      --maintenance) MAINTENANCE_WINDOW=1 ;;
      -*) die "unknown deploy-config flag: $1" ;;
      *) bundle="$1" ;;
    esac
    shift
  done
  [[ -n "$bundle" ]] || die "usage: fleet.sh deploy-config [--dry-run] <bundle-dir>"
  resolve_sandbox_enablement
  parse_conf
  load_bundle_meta "$bundle"
  [[ "$BUNDLE_KIND" == config ]] \
    || die "bundle $BUNDLE_VER is an app release; use: fleet.sh deploy $bundle"
  (( DRY )) && info "DRY-RUN — no host will be touched"
  step "deploy config release=$BUNDLE_VER from $BUNDLE"
  if (( ! DRY )); then
    "$SCRIPT_DIR/verify-bundle.sh" "$BUNDLE" || die "config bundle checksum verification failed"
  else
    info "would run verify-bundle.sh $BUNDLE"
  fi
  if (( MAINTENANCE_WINDOW && ! DRY )); then
    fleet_maintenance on "Deploying config release $BUNDLE_VER" \
      || die "failed to enable maintenance mode"
  fi

  local hosts; hosts="$(all_hosts)"
  if [[ "$hosts" == local ]]; then
    deploy_config_single_local
  else
    deploy_config_multi_host
  fi

  local prev; prev="$(state_get current)"
  if (( DRY )); then
    info "dry-run complete — plan above, nothing changed"
  else
    state_write "$BUNDLE_VER" "${prev:-}" \
      || die "config is healthy but failed to record Fleet state in $STATE_FILE"
    if (( MAINTENANCE_WINDOW )); then
      fleet_maintenance off \
        || die "config is healthy but maintenance mode could not be disabled"
    fi
    ok "config release $BUNDLE_VER live (app images unchanged)"
  fi
}

cmd_prepare_sandbox() {
  local bundle=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY=1 ;;
      -*) die "unknown prepare-sandbox flag: $1" ;;
      *) bundle="$1" ;;
    esac
    shift
  done
  [[ -n "$bundle" ]] || die "usage: fleet.sh prepare-sandbox [--dry-run] <bundle-dir>"
  load_bundle_meta "$bundle"
  [[ "$BUNDLE_KIND" == app ]] || die "config-only bundles do not carry sandbox artifacts"
  (( DRY )) && info "DRY-RUN — sandbox host will not be touched"
  assert_arch local ""
  prepare_sandbox_single_local 1
}

app_scale_for_host() {
  local host="$1" i
  for i in $(app_indices); do
    [[ "${HOST[$i]}" == "$host" ]] \
      && { printf '%s\n' "${SCALE[$i]:-1}"; return 0; }
  done
  return 1
}

single_app_scale() {
  app_scale_for_host local
}

restore_release_single_local() {  # best-effort recovery; caller reports failure
  local release_id="$1" release_root app_ver n
  [[ -n "$release_id" ]] || return 1
  release_root="$(release_dir_for "$ROOT" "$release_id")"
  [[ -f "$release_root/.af-release" ]] || return 1
  app_ver="$(release_app_version_local "$release_root")"
  n="$(single_app_scale)"
  info "attempting automatic restore of last successful release $release_id"
  if has_infra; then
    compose_on_release local "$release_id" "$app_ver" --profile infra up -d --remove-orphans --scale "backend=$n" \
      || return 1
  else
    compose_on_release local "$release_id" "$app_ver" up -d --remove-orphans --scale "backend=$n" \
      || return 1
  fi
  wait_ready_result local
}

# single box: every role local. Compose owns ordering (release gate +
# healthchecks), while Fleet keeps config/deploy in an immutable release root.
deploy_single_local() {
  local n previous_release="" up_ok=0
  assert_arch local "$(host_arch local || true)"
  stage_app_release_local
  previous_release="$(state_get current)"

  step "load app images ($(basename "$APP_TAR"))"
  if (( DRY )); then info "would: docker load -i $APP_TAR"; else docker load -i "$APP_TAR" || die "docker load failed"; ok "images loaded"; fi

  if (( DRY )); then
    info "would verify artifactflow:$BUNDLE_VER embeds $BUNDLE_SANDBOX_IMAGE"
  else
    assert_app_sandbox_image_local
    ok "backend image pins $BUNDLE_SANDBOX_IMAGE"
  fi

  if has_infra; then
    local infra_tar; infra_tar="$(ls "$BUNDLE"/artifactflow-infra-*.tar.gz 2>/dev/null | head -1 || true)"
    if [[ -n "$infra_tar" ]]; then
      step "load infra images ($(basename "$infra_tar"))"
      if (( DRY )); then info "would: docker load -i $infra_tar"; else docker load -i "$infra_tar" || die "infra load failed"; ok "infra images loaded"; fi
    else
      info "no infra tar in bundle — assuming caddy/pg/redis images already loaded"
    fi
  fi

  require_sandbox_ready_single_local

  step "deployment config check"
  if (( DRY )); then
    info "would: AF_VERSION=$BUNDLE_VER AF_WITH_INFRA=$(has_infra && printf 1 || printf 0) AF_ENABLE_SANDBOX=$ENABLE_SANDBOX deploy/scripts/prepare-host.sh check"
  else
    run_prepare_host_check local || die "prepare-host check failed"
  fi

  # scale for the (single) app row
  n="$(single_app_scale)"

  # Intranet Caddy hard-references deploy/certs/{server.crt,server.key}; a
  # missing pem makes it fail config load and never start. Ensure a cert exists
  # (self-signed placeholder if none) before `up`. Idempotent — never clobbers a
  # real cert. See deploy/scripts/ensure-cert.sh.
  step "ensure intranet TLS cert present"
  if (( DRY )); then
    info "would: deploy/scripts/ensure-cert.sh (generate self-signed placeholder if certs/ empty)"
  else
    "$SCRIPT_DIR/ensure-cert.sh" || die "cert bootstrap failed — see message above"
  fi

  if [[ "$ENABLE_SANDBOX" == 1 ]]; then
    if (( DRY )); then
      tar tzf "$BUNDLE/artifactflow-deploy-${BUNDLE_VER}.tar.gz" 2>/dev/null \
        | grep -qx "deploy/$SANDBOX_COMPOSE_BASENAME" \
        || die "AF_ENABLE_SANDBOX=1 but deploy bundle lacks $SANDBOX_COMPOSE_BASENAME"
      info "would include $SANDBOX_COMPOSE_BASENAME from the deploy bundle"
      info "would require: runsc registered, $BUNDLE_SANDBOX_IMAGE loaded, scratch root mounted"
    else
      [[ -f "$STAGED_RELEASE/deploy/$SANDBOX_COMPOSE_BASENAME" ]] \
        || die "AF_ENABLE_SANDBOX=1 but staged release lacks $SANDBOX_COMPOSE_BASENAME"
      command -v runsc >/dev/null 2>&1 || die "AF_ENABLE_SANDBOX=1 but runsc is missing; run fleet.sh prepare-sandbox <bundle-dir> or deploy/scripts/prepare-host.sh sandbox"
      docker info 2>/dev/null | grep -q runsc || die "AF_ENABLE_SANDBOX=1 but Docker runtime 'runsc' is not registered"
      docker image inspect "$BUNDLE_SANDBOX_IMAGE" >/dev/null 2>&1 \
        || die "required sandbox image $BUNDLE_SANDBOX_IMAGE is not loaded; rerun release with --with-sandbox or prepare that exact image"
      local scratch; scratch="$(sandbox_scratch_root_local)"
      findmnt -rn "$scratch" >/dev/null || die "AF_ENABLE_SANDBOX=1 but scratch root is not mounted: $scratch"
    fi
  fi

  step "compose up from immutable release (infra=$(has_infra && printf 1 || printf 0) scale=$n sandbox=${ENABLE_SANDBOX})"
  if (( DRY )); then
    info "would: compose release=$BUNDLE_VER app=$BUNDLE_VER up -d --remove-orphans --scale backend=$n"
    info "would: wait for /health/ready, then smoke"
    return 0
  fi

  if has_infra; then
    compose_on_release local "$BUNDLE_VER" "$BUNDLE_VER" --profile infra up -d --remove-orphans --scale "backend=$n" \
      && up_ok=1
  else
    compose_on_release local "$BUNDLE_VER" "$BUNDLE_VER" up -d --remove-orphans --scale "backend=$n" \
      && up_ok=1
  fi
  if (( ! up_ok )); then
    restore_release_single_local "$previous_release" \
      || bad "automatic restore unavailable/failed; maintenance mode should remain enabled"
    die "compose up failed (release gate may have aborted — inspect release/backend logs)"
  fi
  ok "stack up"
  if ! wait_ready_result local; then
    restore_release_single_local "$previous_release" \
      || bad "automatic restore unavailable/failed; maintenance mode should remain enabled"
    die "new release failed readiness; restored last successful release when possible"
  fi
  smoke local
  activate_release_local "$STAGED_RELEASE"
}

host_override_file() { printf '%s/.env.%s\n' "$DEPLOY_DIR" "$1"; }

host_env_value() {  # host_env_value <host> <key>
  local host="$1" key="$2" value="" override
  # `local` is the control host itself, so deploy/.env is already its concrete
  # target env. Per-host overlays are only for materializing remote host files;
  # applying .env.local here would also pollute the common base used for the
  # next remote host.
  if ! is_local "$host"; then
    override="$(host_override_file "$host")"
    value="$(env_file_value "$override" "$key" || true)"
  fi
  [[ -n "$value" ]] || value="$(env_file_value "$DEPLOY_DIR/.env" "$key" || true)"
  printf '%s\n' "$value"
}

render_host_env() {  # render_host_env <host> <output>
  local host="$1" output="$2" override line key value
  cp "$DEPLOY_DIR/.env" "$output" || die "failed to stage base .env for $host"
  override="$(host_override_file "$host")"
  if ! is_local "$host" && [[ -f "$override" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[[:space:]]*# || -z "$line" || "$line" != *=* ]] && continue
      key="${line%%=*}"; value="${line#*=}"
      env_file_set "$output" "$key" "$value"
    done < "$override"
  fi
  chmod 600 "$output"
}

copy_release_file() {  # copy_release_file <host> <file> <remote-bundle>
  local host="$1" file="$2" remote_bundle="$3"
  [[ -f "$file" ]] || die "required release file missing: $file"
  if (( DRY )); then
    info "  [$host] would copy $(basename "$file")"
  else
    copy_to "$host" "$file" "$remote_bundle/$(basename "$file")" \
      || die "$host: failed to copy $(basename "$file")"
  fi
}

prepare_release_host() {  # prepare_release_host <host>
  local host="$1" dir remote_bundle deploy_tar config_tar manifest env_tmp digest infra_tar=""
  dir="$(target_dir "$host")"
  remote_bundle="$dir/.artifactflow/bundles/$BUNDLE_VER"
  deploy_tar="$BUNDLE/artifactflow-deploy-${BUNDLE_VER}.tar.gz"
  config_tar="$BUNDLE/artifactflow-config-${BUNDLE_VER}.tar.gz"
  manifest="$BUNDLE_MANIFEST"
  digest="$(release_identity_digest app "$APP_TAR" "$deploy_tar" "$config_tar")"

  step "prepare release host $host"
  if (( DRY )); then
    info "  would create $remote_bundle and immutable release root"
  else
    run_on "$host" "mkdir -p '$remote_bundle' '$dir/deploy' '$dir/.artifactflow/releases'" \
      || die "$host: cannot create deployment directories"
  fi
  copy_release_file "$host" "$deploy_tar" "$remote_bundle"
  copy_release_file "$host" "$deploy_tar.sha256" "$remote_bundle"
  copy_release_file "$host" "$config_tar" "$remote_bundle"
  copy_release_file "$host" "$config_tar.sha256" "$remote_bundle"
  copy_release_file "$host" "$manifest" "$remote_bundle"

  if host_has_role "$host" app || host_has_role "$host" release; then
    copy_release_file "$host" "$APP_TAR" "$remote_bundle"
    copy_release_file "$host" "$APP_TAR.sha256" "$remote_bundle"
  fi
  if host_has_role "$host" infra || host_has_role "$host" lb; then
    infra_tar="$(find "$BUNDLE" -maxdepth 1 -type f -name 'artifactflow-infra-*.tar.gz' -print | head -1)"
    if [[ -n "$infra_tar" ]]; then
      copy_release_file "$host" "$infra_tar" "$remote_bundle"
      copy_release_file "$host" "$infra_tar.sha256" "$remote_bundle"
    fi
  fi

  if (( DRY )); then
    info "  [$host] would merge deploy/.env + optional .env.$host"
    info "  [$host] would verify, bootstrap deploy scripts, stage release, and load role images"
    return 0
  fi

  env_tmp="$(mktemp "/tmp/artifactflow-env-${BUNDLE_VER}.XXXXXX")" \
    || die "failed to create host env staging file"
  render_host_env "$host" "$env_tmp"
  copy_to "$host" "$env_tmp" "$dir/deploy/.env" \
    || { rm -f "$env_tmp"; die "$host: failed to install deploy/.env"; }
  rm -f "$env_tmp"
  run_on "$host" "chmod 600 '$dir/deploy/.env'; cd '$remote_bundle'; sha256sum -c '$(basename "$deploy_tar.sha256")'; tar xzf '$(basename "$deploy_tar")' -C '$dir'; '$dir/deploy/scripts/verify-bundle.sh' '$remote_bundle'" \
    || die "$host: transferred bundle verification/bootstrap failed"

  # Stage without touching the active symlink. Version collision with different
  # content is a loud failure, never an overwrite.
  run_on "$host" "set -eu; release='$dir/.artifactflow/releases/$BUNDLE_VER'; if [ -d \"\$release\" ]; then grep -qx 'bundle_digest=$digest' \"\$release/.af-release\"; else tmp=\$(mktemp -d '$dir/.artifactflow/releases/.${BUNDLE_VER}.tmp.XXXXXX'); tar xzf '$remote_bundle/$(basename "$deploy_tar")' -C \"\$tmp\"; tar xzf '$remote_bundle/$(basename "$config_tar")' -C \"\$tmp\"; test -f \"\$tmp/deploy/$COMPOSE_BASENAME\"; test -d \"\$tmp/config\"; printf 'release_id=%s\nkind=app\napp_version=%s\nbundle_digest=%s\n' '$BUNDLE_VER' '$BUNDLE_VER' '$digest' > \"\$tmp/.af-release\"; mv \"\$tmp\" \"\$release\"; fi" \
    || die "$host: immutable release staging failed"

  if host_has_role "$host" app || host_has_role "$host" release; then
    run_on "$host" "docker load -i '$remote_bundle/$(basename "$APP_TAR")'" \
      || die "$host: app image load failed"
    run_on "$host" "actual=\$(docker image inspect 'artifactflow:$BUNDLE_VER' --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | awk -F= '\$1 == \"ARTIFACTFLOW_SANDBOX_IMAGE\" {sub(/^[^=]*=/, \"\"); print; exit}'); test \"\$actual\" = '$BUNDLE_SANDBOX_IMAGE'" \
      || die "$host: backend image sandbox reference does not match the release manifest"
  fi
  if [[ -n "$infra_tar" ]]; then
    run_on "$host" "docker load -i '$remote_bundle/$(basename "$infra_tar")'" \
      || die "$host: infra image load failed"
  fi
  run_prepare_host_check "$host" || die "$host: prepare-host check failed"
}

wait_app_host() {  # wait_app_host <host>
  local host="$1" waited=0 backend_port frontend_port
  backend_port="$(host_env_value "$host" AF_BACKEND_PORT)"; backend_port="${backend_port:-8000}"
  frontend_port="$(host_env_value "$host" AF_FRONTEND_PORT)"; frontend_port="${frontend_port:-3000}"
  while (( waited < READY_TIMEOUT )); do
    if run_on "$host" "curl -fsS --max-time 5 'http://localhost:$backend_port/health/ready' >/dev/null && curl -fsS --max-time 5 'http://localhost:$frontend_port/' >/dev/null" 2>/dev/null; then
      ok "$host app ready after ${waited}s"
      return 0
    fi
    sleep 3; waited=$((waited + 3))
  done
  bad "$host app not ready within ${READY_TIMEOUT}s"
  return 1
}

render_multi_host_upstreams() {
  local i h address backend_port frontend_port
  echo '(backend_upstream_targets) {'
  printf '\tto'
  for i in $(app_indices); do
    h="${HOST[$i]}"; address="${ADVERTISE[$i]}"
    backend_port="$(host_env_value "$h" AF_BACKEND_PORT)"; backend_port="${backend_port:-8000}"
    printf ' %s:%s' "$address" "$backend_port"
  done
  printf '\n}\n\n'
  echo '(frontend_upstream_targets) {'
  printf '\tto'
  for i in $(app_indices); do
    h="${HOST[$i]}"; address="${ADVERTISE[$i]}"
    frontend_port="$(host_env_value "$h" AF_FRONTEND_PORT)"; frontend_port="${frontend_port:-3000}"
    printf ' %s:%s' "$address" "$frontend_port"
  done
  printf '\n}\n'
}

write_multi_host_upstreams() {  # write static targets into LB release
  local lb="$1" release_id="$2" tmp remote
  tmp="$(mktemp /tmp/artifactflow-upstreams.XXXXXX)" || die "failed to create upstream config"
  render_multi_host_upstreams > "$tmp"
  remote="$(release_dir_for "$(target_dir "$lb")" "$release_id")/deploy/caddy/upstreams.caddy"
  if (( DRY )); then
    info "  [$lb] would render static Caddy upstreams: $(tr '\n' ' ' < "$tmp")"
  else
    copy_to "$lb" "$tmp" "$remote" || { rm -f "$tmp"; die "$lb: failed to install static upstreams"; }
  fi
  rm -f "$tmp"
}

activate_release_on() {  # activate_release_on <host> <release-id>
  local host="$1" release_id="$2" dir release link tmp
  dir="$(target_dir "$host")"; release="$(release_dir_for "$dir" "$release_id")"
  link="$dir/.artifactflow/current"; tmp="$link.tmp.$$"
  run_on "$host" "ln -s '$release' '$tmp' && mv -Tf '$tmp' '$link'" \
    || { bad "$host: failed to activate release $release_id"; return 1; }
}

restore_multi_host_release() {  # best effort after a failed rollout
  local release_id="$1" release_root app_ver infra_host release_host lb i h
  [[ -n "$release_id" ]] || return 1
  release_root="$(release_dir_for "$ROOT" "$release_id")"
  [[ -f "$release_root/.af-release" ]] || return 1
  app_ver="$(release_app_version_local "$release_root")"
  info "attempting fleet-wide restore to $release_id"
  infra_host="$(role_host infra || true)"
  [[ -z "$infra_host" ]] || compose_on_release "$infra_host" "$release_id" "$app_ver" --profile infra up -d postgres redis || true
  release_host="$(role_host release)"
  compose_on_release "$release_host" "$release_id" "$app_ver" run --rm --no-deps release || true
  for i in $(app_indices); do
    h="${HOST[$i]}"
    compose_on_release "$h" "$release_id" "$app_ver" up -d --no-deps --force-recreate backend frontend || true
  done
  lb="$(role_host lb)"
  compose_on_release "$lb" "$release_id" "$app_ver" up -d --no-deps --force-recreate caddy || true
  while IFS= read -r h; do activate_release_on "$h" "$release_id" || true; done < <(all_hosts)
  wait_ready_result "$lb"
}

# Multi-host transport and ordering. This path is intentionally conservative:
# exactly one backend per app host until per-replica port discovery exists.
deploy_multi_host() {
  local i h n infra_host release_host lb previous_release
  previous_release="$(state_get current)"
  for i in $(app_indices); do
    n="${SCALE[$i]:-1}"
    [[ "$n" == 1 ]] || die "multi-host app row ${HOST[$i]} must use scale=1 (got $n); add service discovery before scaling within a host"
  done
  info "MULTI-HOST path is executable but awaits first physical acceptance run; proceeding without the former hard gate"
  stage_app_release_local  # control-plane copy for state/rollback metadata

  while IFS= read -r h; do
    assert_arch "$h" "$(host_arch "$h" || true)"
    prepare_release_host "$h"
  done < <(all_hosts)
  (( DRY )) && {
    write_multi_host_upstreams "$(role_host lb)" "$BUNDLE_VER"
    info "would run infra → release gate → app hosts one-by-one → LB → smoke → activate"
    return 0
  }

  infra_host="$(role_host infra || true)"
  if [[ -n "$infra_host" ]]; then
    step "start infra on $infra_host"
    compose_on_release "$infra_host" "$BUNDLE_VER" "$BUNDLE_VER" --profile infra up -d postgres redis \
      || { restore_multi_host_release "$previous_release" || true; die "infra rollout failed"; }
  fi

  release_host="$(role_host release)"
  step "release/reconcile gate on $release_host"
  compose_on_release "$release_host" "$BUNDLE_VER" "$BUNDLE_VER" run --rm --no-deps release \
    || { restore_multi_host_release "$previous_release" || true; die "release gate failed"; }

  for i in $(app_indices); do
    h="${HOST[$i]}"
    step "roll app host $h"
    compose_on_release "$h" "$BUNDLE_VER" "$BUNDLE_VER" up -d --no-deps --force-recreate backend frontend \
      || { restore_multi_host_release "$previous_release" || true; die "$h app rollout failed"; }
    wait_app_host "$h" \
      || { restore_multi_host_release "$previous_release" || true; die "$h failed readiness"; }
  done

  lb="$(role_host lb)"
  write_multi_host_upstreams "$lb" "$BUNDLE_VER"
  run_on "$lb" "cd '$(target_dir "$lb")' && deploy/scripts/ensure-cert.sh" \
    || { restore_multi_host_release "$previous_release" || true; die "$lb certificate bootstrap failed"; }
  step "roll LB host $lb"
  compose_on_release "$lb" "$BUNDLE_VER" "$BUNDLE_VER" up -d --no-deps --force-recreate caddy \
    || { restore_multi_host_release "$previous_release" || true; die "$lb rollout failed"; }
  wait_ready_result "$lb" \
    || { restore_multi_host_release "$previous_release" || true; die "fleet LB readiness failed"; }
  smoke "$lb"

  while IFS= read -r h; do
    activate_release_on "$h" "$BUNDLE_VER" \
      || { restore_multi_host_release "$previous_release" || true; die "$h release activation failed"; }
  done < <(all_hosts)
  activate_release_local "$STAGED_RELEASE"
}

prepare_config_host() {  # prepare_config_host <host> <previous-release> <app-version>
  local host="$1" previous="$2" app_ver="$3" dir remote_bundle config_tar digest release
  dir="$(target_dir "$host")"
  remote_bundle="$dir/.artifactflow/bundles/$BUNDLE_VER"
  config_tar="$BUNDLE/artifactflow-config-${BUNDLE_VER}.tar.gz"
  digest="$(release_identity_digest "base:$previous" "$config_tar")"
  release="$(release_dir_for "$dir" "$BUNDLE_VER")"
  step "prepare config release on $host"
  if (( DRY )); then
    info "  [$host] would copy/verify config and clone deploy from $previous"
    return 0
  fi
  run_on "$host" "mkdir -p '$remote_bundle' '$dir/.artifactflow/releases'" \
    || die "$host: cannot create config bundle directory"
  copy_release_file "$host" "$config_tar" "$remote_bundle"
  copy_release_file "$host" "$config_tar.sha256" "$remote_bundle"
  copy_release_file "$host" "$BUNDLE_MANIFEST" "$remote_bundle"
  run_on "$host" "cd '$remote_bundle' && sha256sum -c '$(basename "$config_tar.sha256")'" \
    || die "$host: config transfer checksum failed"
  run_on "$host" "set -eu; release='$release'; previous='$(release_dir_for "$dir" "$previous")'; test -f \"\$previous/.af-release\"; if [ -d \"\$release\" ]; then grep -qx 'bundle_digest=$digest' \"\$release/.af-release\"; else tmp=\$(mktemp -d '$dir/.artifactflow/releases/.${BUNDLE_VER}.tmp.XXXXXX'); cp -a \"\$previous/deploy\" \"\$tmp/deploy\"; tar xzf '$remote_bundle/$(basename "$config_tar")' -C \"\$tmp\"; test -d \"\$tmp/config\"; printf 'release_id=%s\nkind=config\napp_version=%s\nbundle_digest=%s\n' '$BUNDLE_VER' '$app_ver' '$digest' > \"\$tmp/.af-release\"; mv \"\$tmp\" \"\$release\"; fi" \
    || die "$host: config release staging failed"
}

deploy_config_multi_host() {
  local previous_release app_ver release_host lb i h n
  previous_release="$(state_get current)"
  [[ -n "$previous_release" ]] || die "multi-host deploy-config needs a previous successful release"
  for i in $(app_indices); do
    n="${SCALE[$i]:-1}"
    [[ "$n" == 1 ]] || die "multi-host deploy-config requires scale=1 per app host (got $n on ${HOST[$i]})"
  done
  stage_config_release_local
  app_ver="$(release_app_version_local "$STAGED_RELEASE")"
  info "MULTI-HOST config path awaits physical acceptance; proceeding without the former hard gate"
  while IFS= read -r h; do
    prepare_config_host "$h" "$previous_release" "$app_ver"
  done < <(all_hosts)
  if (( DRY )); then
    info "would reconcile once → roll app hosts → LB smoke → activate config release"
    return 0
  fi

  release_host="$(role_host release)"
  compose_on_release "$release_host" "$BUNDLE_VER" "$app_ver" run --rm --no-deps release \
    || die "config release gate failed; no host was activated"
  for i in $(app_indices); do
    h="${HOST[$i]}"
    compose_on_release "$h" "$BUNDLE_VER" "$app_ver" up -d --no-deps --force-recreate backend frontend \
      || { restore_multi_host_release "$previous_release" || true; die "$h config rollout failed"; }
    wait_app_host "$h" \
      || { restore_multi_host_release "$previous_release" || true; die "$h config readiness failed"; }
  done
  lb="$(role_host lb)"
  wait_ready_result "$lb" \
    || { restore_multi_host_release "$previous_release" || true; die "config rollout LB readiness failed"; }
  smoke "$lb"
  while IFS= read -r h; do
    activate_release_on "$h" "$BUNDLE_VER" || die "$h config activation failed"
  done < <(all_hosts)
  activate_release_local "$STAGED_RELEASE"
}

rollback_multi_host() {  # rollback_multi_host <target-release> <current-release>
  local target="$1" current="$2" release_root app_ver infra_host release_host lb i h
  release_root="$(release_dir_for "$ROOT" "$target")"
  [[ -f "$release_root/.af-release" ]] || die "control-plane release snapshot missing: $release_root"
  app_ver="$(release_app_version_local "$release_root")"
  while IFS= read -r h; do
    run_on "$h" "test -f '$(release_dir_for "$(target_dir "$h")" "$target")/.af-release'" \
      || die "$h: rollback snapshot $target missing"
  done < <(all_hosts)
  if (( DRY )); then
    info "would roll every role to release=$target app=$app_ver, smoke, then activate"
    return 0
  fi

  infra_host="$(role_host infra || true)"
  [[ -z "$infra_host" ]] || compose_on_release "$infra_host" "$target" "$app_ver" --profile infra up -d postgres redis \
    || die "rollback infra failed"
  release_host="$(role_host release)"
  compose_on_release "$release_host" "$target" "$app_ver" run --rm --no-deps release \
    || die "rollback release/reconcile gate failed"
  for i in $(app_indices); do
    h="${HOST[$i]}"
    compose_on_release "$h" "$target" "$app_ver" up -d --no-deps --force-recreate backend frontend \
      || die "$h rollback failed"
    wait_app_host "$h" || die "$h rollback readiness failed"
  done
  lb="$(role_host lb)"
  compose_on_release "$lb" "$target" "$app_ver" up -d --no-deps --force-recreate caddy \
    || die "$lb rollback failed"
  wait_ready "$lb"
  smoke "$lb"
  while IFS= read -r h; do activate_release_on "$h" "$target" || die "$h rollback activation failed"; done < <(all_hosts)
  activate_release_local "$release_root"
  state_write "$target" "$current" \
    || die "rollback is healthy but failed to record Fleet state in $STATE_FILE"
  ok "rolled back fleet to $target (app=$app_ver, config/deploy restored)"
}

validate_env_candidate() {
  local file="$1" key value fail=0
  [[ -f "$file" ]] || die "env file not found: $file"
  for key in ARTIFACTFLOW_JWT_SECRET ARTIFACTFLOW_CREDENTIAL_KEY ARTIFACTFLOW_REDIS_URL ARTIFACTFLOW_REDIS_KEY_PREFIX; do
    value="$(env_file_value "$file" "$key" || true)"
    [[ -n "$value" && "$value" != *CHANGE_ME* ]] \
      || { bad "$file: missing/placeholder $key"; fail=1; }
  done
  value="$(env_file_value "$file" ARTIFACTFLOW_DATABASE_URLS || true)"
  [[ -n "$value" ]] || value="$(env_file_value "$file" ARTIFACTFLOW_DATABASE_URL || true)"
  [[ -n "$value" ]] || { bad "$file: database URL missing"; fail=1; }
  value="$(env_file_value "$file" AF_ENABLE_SANDBOX || true)"
  [[ "$value" == 0 || "$value" == 1 ]] || { bad "$file: AF_ENABLE_SANDBOX must be 0 or 1"; fail=1; }
  (( fail == 0 )) || die "env validation failed"
  ok "env candidate basic validation passed"
}

install_env_local() {  # install_env_local <candidate>
  local candidate="$1" tmp
  tmp="$(mktemp "$DEPLOY_DIR/.env.tmp.XXXXXX")" || die "cannot stage deploy/.env"
  cp "$candidate" "$tmp" || { rm -f "$tmp"; die "cannot copy env candidate"; }
  chmod 600 "$tmp" || { rm -f "$tmp"; die "cannot secure env candidate"; }
  mv "$tmp" "$DEPLOY_DIR/.env" || die "cannot activate deploy/.env"
}

restore_remote_envs() {
  local h dir
  while IFS= read -r h; do
    is_local "$h" && continue
    dir="$(target_dir "$h")"
    run_on "$h" "if [ -f '$dir/deploy/.env.fleet-prev' ]; then mv -f '$dir/deploy/.env.fleet-prev' '$dir/deploy/.env'; chmod 600 '$dir/deploy/.env'; fi" || true
  done < <(all_hosts)
}

cmd_env() {
  local action="${1:-}" candidate="${2:-$DEPLOY_DIR/.env}" cur release_root app_ver backup h dir tmp infra_host release_host lb i n
  case "$action" in check|apply) ;; *) die "usage: fleet.sh env {check|apply} [env-file]" ;; esac
  validate_env_candidate "$candidate"
  [[ "$action" == check ]] && return 0
  parse_conf
  cur="$(state_get current)"
  [[ -n "$cur" ]] || die "env apply requires a successful immutable release"
  release_root="$(release_dir_for "$ROOT" "$cur")"
  [[ -f "$release_root/.af-release" ]] || die "active release snapshot missing: $release_root"
  app_ver="$(release_app_version_local "$release_root")"
  backup="$(mktemp /tmp/artifactflow-env-backup.XXXXXX)" || die "cannot create env backup"
  cp "$DEPLOY_DIR/.env" "$backup" || { rm -f "$backup"; die "cannot back up deploy/.env"; }
  install_env_local "$candidate"
  resolve_sandbox_enablement

  if [[ "$(all_hosts)" == local ]]; then
    run_prepare_host_check local \
      || { install_env_local "$backup"; rm -f "$backup"; die "new env failed host validation; restored old env"; }
    n="$(single_app_scale)"
    step "apply env by reconciling/recreating the active release"
    if has_infra; then
      compose_on_release local "$cur" "$app_ver" --profile infra up -d --remove-orphans --scale "backend=$n" \
        || { install_env_local "$backup"; restore_release_single_local "$cur" || true; rm -f "$backup"; die "env apply failed; old env restored"; }
    else
      compose_on_release local "$cur" "$app_ver" up -d --remove-orphans --scale "backend=$n" \
        || { install_env_local "$backup"; restore_release_single_local "$cur" || true; rm -f "$backup"; die "env apply failed; old env restored"; }
    fi
    wait_ready_result local \
      || { install_env_local "$backup"; restore_release_single_local "$cur" || true; rm -f "$backup"; die "env readiness failed; old env restored"; }
  else
    for i in $(app_indices); do
      n="${SCALE[$i]:-1}"; [[ "$n" == 1 ]] || die "multi-host env apply requires scale=1 on ${HOST[$i]}"
    done
    while IFS= read -r h; do
      if is_local "$h"; then
        run_prepare_host_check "$h" \
          || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; rm -f "$backup"; die "$h: new env validation failed"; }
        continue
      fi
      dir="$(target_dir "$h")"
      run_on "$h" "cp '$dir/deploy/.env' '$dir/deploy/.env.fleet-prev'" \
        || { install_env_local "$backup"; restore_remote_envs; rm -f "$backup"; die "$h: cannot back up env"; }
      tmp="$(mktemp /tmp/artifactflow-host-env.XXXXXX)" \
        || { install_env_local "$backup"; restore_remote_envs; rm -f "$backup"; die "cannot stage host env"; }
      render_host_env "$h" "$tmp"
      copy_to "$h" "$tmp" "$dir/deploy/.env" \
        || { rm -f "$tmp"; install_env_local "$backup"; restore_remote_envs; rm -f "$backup"; die "$h: env copy failed"; }
      rm -f "$tmp"
      run_on "$h" "chmod 600 '$dir/deploy/.env'" \
        || { install_env_local "$backup"; restore_remote_envs; rm -f "$backup"; die "$h: cannot secure env"; }
      run_prepare_host_check "$h" \
        || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; rm -f "$backup"; die "$h: new env validation failed"; }
    done < <(all_hosts)
    infra_host="$(role_host infra || true)"
    [[ -z "$infra_host" ]] || compose_on_release "$infra_host" "$cur" "$app_ver" --profile infra up -d postgres redis \
      || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "infra env apply failed"; }
    release_host="$(role_host release)"
    compose_on_release "$release_host" "$cur" "$app_ver" run --rm --no-deps release \
      || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "env release gate failed"; }
    for i in $(app_indices); do
      h="${HOST[$i]}"
      compose_on_release "$h" "$cur" "$app_ver" up -d --no-deps --force-recreate backend frontend \
        || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "$h env rollout failed"; }
      wait_app_host "$h" || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "$h env readiness failed"; }
    done
    lb="$(role_host lb)"
    compose_on_release "$lb" "$cur" "$app_ver" up -d --no-deps --force-recreate caddy \
      || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "LB env apply failed"; }
    wait_ready_result "$lb" \
      || { install_env_local "$backup"; restore_remote_envs; restore_multi_host_release "$cur" || true; die "env apply LB readiness failed"; }
    while IFS= read -r h; do dir="$(target_dir "$h")"; run_on "$h" "rm -f '$dir/deploy/.env.fleet-prev'" || true; done < <(all_hosts)
  fi
  rm -f "$backup"
  ok "environment applied to release $cur (app=$app_ver)"
}

cmd_proxy_reload() {
  resolve_sandbox_enablement
  parse_conf
  local cur release_root app_ver lb
  cur="$(state_get current)"; [[ -n "$cur" ]] || die "no active release"
  release_root="$(release_dir_for "$ROOT" "$cur")"
  app_ver="$(release_app_version_local "$release_root")"; app_ver="${app_ver:-latest}"
  lb="$(role_host lb)"
  compose_on_release "$lb" "$cur" "$app_ver" exec caddy caddy reload \
    --config /etc/caddy/conf/Caddyfile.intranet --adapter caddyfile \
    || die "Caddy reload failed on $lb"
  ok "Caddy config/cert reloaded on $lb"
}

cmd_maintenance() {
  local action="${1:-status}" note="${2:-}"
  case "$action" in on|off|status) ;; *) die "usage: fleet.sh maintenance {on|off|status} [note]" ;; esac
  parse_conf
  fleet_maintenance "$action" "$note"
}

# ════════════════════════════════════════════════════════════════════
# status
# ════════════════════════════════════════════════════════════════════
status_compose_on() {  # status_compose_on <host> <release-id-or-empty> <app-version> <args...>
  local host="$1" release_id="$2" app_ver="$3" dir args; shift 3
  if [[ -n "$release_id" ]]; then
    compose_on_release "$host" "$release_id" "$app_ver" "$@"
  else
    dir="$(target_dir "$host")"; args="$(shell_args "$@")"
    run_on "$host" "cd '$dir/deploy' && docker compose --env-file '$dir/deploy/.env' -f '$dir/deploy/$COMPOSE_BASENAME'$args"
  fi
}

cmd_status() {
  resolve_sandbox_enablement
  parse_conf
  local cur app_ver="latest" fail=0; cur="$(state_get current)"
  if [[ -n "$cur" ]]; then
    local control_release; control_release="$(release_dir_for "$ROOT" "$cur")"
    app_ver="$(release_app_version_local "$control_release")"
    app_ver="${app_ver:-latest}"
  fi
  step "fleet status${cur:+ (deployed version: $cur)}"
  local host
  while IFS= read -r host; do
    printf '\n  \033[1m[%s]\033[0m\n' "$host"
    local ps expected="" svc ids count want cid container_state
    # No `--profile infra` needed: `docker compose ps` lists ALL running project
    # containers regardless of which profiles are active (verified on compose
    # v2), so pg/redis show up here even though they're profile-gated.
    ps="$(status_compose_on "$host" "$cur" "$app_ver" ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null)"
    if [[ -n "$ps" ]]; then
      printf '%s\n' "$ps" | sed 's/^/    /'
    elif host_has_role "$host" release \
      && ! host_has_role "$host" infra \
      && ! host_has_role "$host" app \
      && ! host_has_role "$host" lb; then
      info "release-only host: no long-running service expected"
    else
      info "no compose project up (or unreachable)"
    fi

    host_has_role "$host" infra && expected+=" postgres redis"
    host_has_role "$host" app && expected+=" backend frontend"
    host_has_role "$host" lb && expected+=" caddy"
    for svc in $expected; do
      ids="$(status_compose_on "$host" "$cur" "$app_ver" ps -q "$svc" 2>/dev/null)"
      count="$(printf '%s\n' "$ids" | awk 'NF {n++} END {print n+0}')"
      want=1
      [[ "$svc" == backend ]] && want="$(app_scale_for_host "$host")"
      if [[ "$count" != "$want" ]]; then
        bad "$host: $svc running container count=$count, expected=$want"
        fail=1
        continue
      fi
      while IFS= read -r cid; do
        [[ -n "$cid" ]] || continue
        container_state="$(run_on "$host" "docker inspect -f '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' '$cid'" 2>/dev/null || true)"
        case "$container_state" in
          "true healthy"|"true none") ;;
          *) bad "$host: $svc container ${cid:0:12} state=${container_state:-unreachable}"; fail=1 ;;
        esac
      done <<< "$ids"
    done
  done < <(all_hosts)
  # health via LB
  local lb; lb="$(role_host lb)"
  echo
  if run_on "$lb" "$(lb_ready_cmd "$lb")" 2>/dev/null; then
    ok "LB /health/ready green ($lb)"
  else
    bad "LB /health/ready NOT green ($lb)"
    fail=1
  fi
  (( fail == 0 ))
}

# ════════════════════════════════════════════════════════════════════
# rollback
# ════════════════════════════════════════════════════════════════════
cmd_rollback() {
  [[ "${1:-}" == "--dry-run" ]] && DRY=1
  resolve_sandbox_enablement
  parse_conf
  local prev cur; prev="$(state_get previous)"; cur="$(state_get current)"
  [[ -n "$prev" ]] || die "no previous version recorded in $STATE_FILE — nothing to roll back to"
  validate_release_id "$prev"
  step "rollback release: $cur → $prev"
  local hosts; hosts="$(all_hosts)"
  if [[ "$hosts" != "local" ]]; then
    rollback_multi_host "$prev" "$cur"
    return 0
  fi
  local release_root app_ver n
  release_root="$(release_dir_for "$ROOT" "$prev")"
  [[ -f "$release_root/.af-release" ]] \
    || die "release snapshot missing: $release_root"
  app_ver="$(release_app_version_local "$release_root")"
  [[ -n "$app_ver" ]] || die "release $prev has no app_version metadata"
  n="$(single_app_scale)"
  if (( DRY )); then
    info "would activate release snapshot=$prev app=$app_ver and reconcile its config"
    return 0
  fi
  if has_infra; then
    compose_on_release local "$prev" "$app_ver" --profile infra up -d --remove-orphans \
      --scale "backend=$n" || die "rollback compose up failed"
  else
    compose_on_release local "$prev" "$app_ver" up -d --remove-orphans \
      --scale "backend=$n" || die "rollback compose up failed"
  fi
  wait_ready local
  smoke local
  activate_release_local "$release_root"
  state_write "$prev" "$cur" \
    || die "rollback is healthy but failed to record Fleet state in $STATE_FILE"
  ok "rolled back full release to $prev (app=$app_ver, config/deploy restored)"
}

# ── dispatch ────────────────────────────────────────────────────────
usage() { sed -n '2,40p' "$0"; }
main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    init-local) cmd_init_local "$@" ;;
    bootstrap) cmd_bootstrap "$@" ;;
    preflight) cmd_preflight "$@" ;;
    deploy)    cmd_deploy "$@" ;;
    deploy-config) cmd_deploy_config "$@" ;;
    config)    "$SCRIPT_DIR/config-hotfix.sh" "$@" ;;
    prepare-sandbox) cmd_prepare_sandbox "$@" ;;
    env)       cmd_env "$@" ;;
    proxy-reload) cmd_proxy_reload "$@" ;;
    maintenance) cmd_maintenance "$@" ;;
    status)    cmd_status "$@" ;;
    rollback)  cmd_rollback "$@" ;;
    ""|-h|--help|help) usage ;;
    *) die "unknown subcommand: $sub (try: bootstrap | init-local | preflight | deploy | deploy-config | config | env | proxy-reload | maintenance | prepare-sandbox | status | rollback)" ;;
  esac
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
