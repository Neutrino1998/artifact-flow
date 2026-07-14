#!/usr/bin/env bash
# fleet.sh — ArtifactFlow unified deploy entrypoint (Phase D, 决策 7).
#
# One command surface for the whole "single box → multi-worker → multi-host"
# continuum. Single machine is the DEGENERATE case, not a separate flow: the
# same deploy sequence runs, the transport layer just resolves `host=local` to
# local execution (no ssh). Running single-box daily keeps the multi-host path
# continuously rehearsed — that's the anti-drift value.
#
# Topology lives in deploy/fleet.conf (copy from fleet.conf.example). Roles:
# infra (pg+redis) / release (one-shot migrate+reconcile) / app (backend×scale
# + frontend) / lb (caddy).
#
# Subcommands:
#   fleet.sh init-local                write single-host fleet.conf + seed deploy/.env
#   fleet.sh preflight                 per-host docker/compose/disk/clock + deploy/.env checks
#   fleet.sh deploy <bundle-dir>       verify → extract → load → release gate → (rolling) up → LB → smoke
#   fleet.sh deploy --dry-run <dir>    print the plan, touch nothing
#   fleet.sh prepare-sandbox <dir>     load sandbox image; install runsc only when bundled
#   fleet.sh status                    per-host `compose ps` + /health/ready probe
#   fleet.sh rollback                  re-up the previously-deployed version (images kept)
#   fleet.sh rollback --dry-run        print the rollback plan
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
# ── Single-host vs multi-host (what's TESTED) ──
# Single-host (all roles `local`) is the near-term path and is Mac-testable end
# to end — it delegates ordering to compose's own depends_on (release gate +
# healthchecks). Multi-host is authored but UNEXERCISED until a 2nd machine:
# cross-host role split needs backend port publishing, a static Caddy upstream
# (not docker-DNS `dynamic a`), and per-host DB/Redis URLs — see deploy/FLEET.md
# "Multi-host: unexercised seams". Those seams loud-fail rather than pretend.

set -uo pipefail  # not -e: we drive the sequence with explicit die() so every
                  # stop point carries a diagnostic, and per-host loops report
                  # which host failed instead of a bare non-zero.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DIR="$ROOT/deploy"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.intranet.yml"
SANDBOX_COMPOSE_FILE="$DEPLOY_DIR/docker-compose.sandbox.yml"
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

# ── fleet.conf parsing → parallel arrays ROLE/HOST/ARCH/SCALE ───────
ROLE=(); HOST=(); ARCH=(); SCALE=()
parse_conf() {
  [[ -f "$FLEET_CONF" ]] || die "fleet.conf not found: $FLEET_CONF (copy from fleet.conf.example)"
  local line role host rest kv a s
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"                       # strip inline comment
    read -r role host rest <<<"$line"        # split first two + remainder
    [[ -z "${role:-}" ]] && continue
    [[ -z "${host:-}" ]] && die "fleet.conf: role '$role' has no host"
    a=""; s=""
    for kv in $rest; do
      case "$kv" in
        arch=*)  a="${kv#arch=}" ;;
        scale=*) s="${kv#scale=}" ;;
        *) die "fleet.conf: unknown field '$kv' on row '$role $host'" ;;
      esac
    done
    case "$role" in infra|release|app|lb) ;; *) die "fleet.conf: bad role '$role'";; esac
    ROLE+=("$role"); HOST+=("$host"); ARCH+=("$a"); SCALE+=("$s")
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
  local host="$1" dir with_infra=0 version_env="" sandbox_env="" expected_version=""
  dir="$(target_dir "$host")"
  host_has_role "$host" infra && with_infra=1
  expected_version="${BUNDLE_VER:-${AF_BUNDLE_VERSION:-}}"
  [[ -n "$expected_version" ]] && version_env="AF_VERSION='$expected_version' "
  [[ -n "${BUNDLE_SANDBOX_IMAGE:-}" ]] && sandbox_env="AF_SANDBOX_IMAGE_REF='$BUNDLE_SANDBOX_IMAGE' "

  run_on "$host" "if [ -d '$dir' ]; then cd '$dir'; else echo '  ℹ prepare-host check skipped ($dir not created yet)'; exit 0; fi; if [ -x deploy/scripts/prepare-host.sh ]; then AF_WITH_INFRA='$with_infra' AF_ENABLE_SANDBOX='$ENABLE_SANDBOX' ${version_env}${sandbox_env}deploy/scripts/prepare-host.sh check; else echo '  ℹ prepare-host check skipped (deploy/scripts/prepare-host.sh not found yet)'; fi"
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

# compose on a host: cd into its deploy dir, export AF_VERSION, run given args.
compose_flags_for_dir() {
  local dir="$1"
  printf -- "-f '%s/deploy/docker-compose.intranet.yml'" "$dir"
  if [[ "$ENABLE_SANDBOX" == 1 ]]; then
    printf -- " -f '%s/deploy/docker-compose.sandbox.yml'" "$dir"
  fi
}

shell_args() {
  local arg
  for arg in "$@"; do
    printf ' %q' "$arg"
  done
}

compose_on() {  # compose_on <host> <version> <compose-args...>
  local host="$1" ver="$2"; shift 2
  local dir flags args
  dir="$(target_dir "$host")"
  flags="$(compose_flags_for_dir "$dir")"
  args="$(shell_args "$@")"
  run_on "$host" "cd '$dir/deploy' && AF_VERSION='$ver' docker compose $flags$args"
}

# ── bundle introspection (manifest is source of truth) ──────────────
BUNDLE=""; BUNDLE_VER=""; BUNDLE_PLATFORM=""; BUNDLE_SANDBOX_IMAGE=""; BUNDLE_GVISOR_PACKAGE=""; APP_TAR=""
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
  BUNDLE_PLATFORM="$(manifest_value "$mf" "Platform")"
  BUNDLE_SANDBOX_IMAGE="$(manifest_value "$mf" "Sandbox image required")"
  BUNDLE_GVISOR_PACKAGE="$(manifest_value "$mf" "gVisor host runtime")"
  [[ -n "$BUNDLE_VER" ]] || die "cannot read version from manifest $mf"
  [[ "$BUNDLE_SANDBOX_IMAGE" =~ ^artifactflow-sandbox:[0-9a-f]{16}-(amd64|arm64)$ ]] \
    || die "manifest has invalid or missing immutable sandbox image reference: $mf"
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
  APP_TAR="$BUNDLE/artifactflow-app-${BUNDLE_VER}.tar.gz"
  [[ -f "$APP_TAR" ]] || die "app tar for version $BUNDLE_VER not found: $APP_TAR"
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

extract_release_units_single_local() {
  local config_tar deploy_tar
  config_tar="$BUNDLE/artifactflow-config-${BUNDLE_VER}.tar.gz"
  deploy_tar="$BUNDLE/artifactflow-deploy-${BUNDLE_VER}.tar.gz"
  [[ -f "$config_tar" ]] || die "config tar for version $BUNDLE_VER not found: $config_tar"
  [[ -f "$deploy_tar" ]] || die "deploy tar for version $BUNDLE_VER not found: $deploy_tar"

  step "extract config/deploy units"
  if (( DRY )); then
    info "would: tar xzf $deploy_tar -C $ROOT"
    info "would: tar xzf $config_tar -C $ROOT"
  else
    tar xzf "$deploy_tar" -C "$ROOT" || die "deploy tar extract failed"
    tar xzf "$config_tar" -C "$ROOT" || die "config tar extract failed"
    ok "config/deploy extracted"
  fi
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
lb_ready_cmd() {  # a curl that returns 0 iff /health/ready is green via the LB
  # self-signed intranet cert → -k; loopback on the lb host's published HTTPS port
  echo "curl -fsk --max-time 5 https://localhost:${HTTPS_PORT}/health/ready >/dev/null"
}
wait_ready() {  # wait_ready <lb-host>
  local host="$1" waited=0 cmd; cmd="$(lb_ready_cmd)"
  step "wait for /health/ready via LB ($host, timeout ${READY_TIMEOUT}s)"
  while (( waited < READY_TIMEOUT )); do
    if run_on "$host" "$cmd" 2>/dev/null; then ok "LB healthy after ${waited}s"; return 0; fi
    sleep 3; waited=$((waited+3))
  done
  die "LB /health/ready not green within ${READY_TIMEOUT}s on $host"
}
smoke() {  # smoke <lb-host>
  local host="$1" cmd; cmd="$(lb_ready_cmd)"
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
cmd_deploy() {
  local bundle=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY=1 ;;
      -*) die "unknown deploy flag: $1" ;;
      *) bundle="$1" ;;
    esac
    shift
  done
  [[ -n "$bundle" ]] || die "usage: fleet.sh deploy [--dry-run] <bundle-dir>"
  resolve_sandbox_enablement
  parse_conf
  load_bundle_meta "$bundle"
  (( DRY )) && info "DRY-RUN — no host will be touched"
  step "deploy version=$BUNDLE_VER platform=$BUNDLE_PLATFORM from $BUNDLE"

  # verify checksums up front (verify-bundle.sh handles the cd dance)
  if (( ! DRY )); then
    step "verify bundle checksums"
    "$SCRIPT_DIR/verify-bundle.sh" "$BUNDLE" || die "bundle checksum verification failed"
  else
    info "would run verify-bundle.sh $BUNDLE"
  fi

  local hosts; hosts="$(all_hosts)"
  local single=0
  [[ "$hosts" == "local" ]] && single=1

  if (( single )); then
    deploy_single_local
  else
    info "MULTI-HOST path — authored but UNEXERCISED (see deploy/FLEET.md). Aborting real run for safety; --dry-run to inspect the plan."
    (( DRY )) || die "multi-host deploy is gated off until validated on a 2nd machine; remove this guard in fleet.sh cmd_deploy after acceptance"
    deploy_multi_host
  fi

  local prev; prev="$(state_get current)"
  echo
  if (( DRY )); then
    info "dry-run complete — plan above, nothing changed"
  else
    state_write "$BUNDLE_VER" "${prev:-}"
    ok "deploy done — version $BUNDLE_VER live${prev:+, previous was $prev}"
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
  (( DRY )) && info "DRY-RUN — sandbox host will not be touched"
  assert_arch local ""
  prepare_sandbox_single_local 1
}

# single box: every role local. compose owns ordering (release gate +
# healthchecks); we just load images and up the whole stack.
deploy_single_local() {
  assert_arch local ""
  extract_release_units_single_local

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
  local i n="" profile=""
  for i in $(app_indices); do n="${SCALE[$i]}"; done
  has_infra && profile="--profile infra"
  local scale_arg=""; [[ -n "$n" ]] && scale_arg="--scale backend=$n"

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
    [[ -f "$SANDBOX_COMPOSE_FILE" ]] || die "AF_ENABLE_SANDBOX=1 but $SANDBOX_COMPOSE_FILE is missing"
    if (( DRY )); then
      info "would require: runsc registered, $BUNDLE_SANDBOX_IMAGE loaded, scratch root mounted"
    else
      command -v runsc >/dev/null 2>&1 || die "AF_ENABLE_SANDBOX=1 but runsc is missing; run fleet.sh prepare-sandbox <bundle-dir> or deploy/scripts/prepare-host.sh sandbox"
      docker info 2>/dev/null | grep -q runsc || die "AF_ENABLE_SANDBOX=1 but Docker runtime 'runsc' is not registered"
      docker image inspect "$BUNDLE_SANDBOX_IMAGE" >/dev/null 2>&1 \
        || die "required sandbox image $BUNDLE_SANDBOX_IMAGE is not loaded; rerun release with --with-sandbox or prepare that exact image"
      local scratch; scratch="$(sandbox_scratch_root_local)"
      findmnt -rn "$scratch" >/dev/null || die "AF_ENABLE_SANDBOX=1 but scratch root is not mounted: $scratch"
    fi
  fi

  local compose_args=(-f "$COMPOSE_FILE")
  [[ "$ENABLE_SANDBOX" == 1 ]] && compose_args+=(-f "$SANDBOX_COMPOSE_FILE")

  step "compose up (profile='${profile:-none}' ${scale_arg:-scale=1} sandbox=${ENABLE_SANDBOX})"
  if (( DRY )); then
    info "would: AF_VERSION=$BUNDLE_VER docker compose ${compose_args[*]} $profile up -d --remove-orphans $scale_arg"
    info "would: wait for /health/ready, then smoke"
    return 0
  fi
  # shellcheck disable=SC2086
  env AF_VERSION="$BUNDLE_VER" docker compose "${compose_args[@]}" $profile up -d --remove-orphans $scale_arg \
    || die "compose up failed (release gate may have aborted — check 'docker compose logs release')"
  ok "stack up"
  wait_ready local
  smoke local
}

# multi-host: fleet.sh owns cross-host ordering (compose depends_on is same-host
# only). Structured but UNEXERCISED — the cross-host seams are marked below.
deploy_multi_host() {
  info "plan for version $BUNDLE_VER:"
  local i h n
  # 1. per-host: copy bundle + load images (+ untar config/deploy on remotes)
  while IFS= read -r h; do
    info "  [$h] scp bundle → docker load app$(has_infra && [[ "$(role_host infra)" == "$h" ]] && echo ' + infra') → untar config/deploy into $(target_dir "$h")"
  done < <(all_hosts)
  # 2. push single-source .env + per-host override
  info "  push deploy/.env (+ per-host .env.<host> override) to every host"
  # 3. release gate on the one release host
  info "  [$(role_host release)] docker compose run --rm --no-deps release  (must exit 0)"
  # 4. rolling app up, host by host
  for i in $(app_indices); do
    h="${HOST[$i]}"; n="${SCALE[$i]:-1}"
    info "  [$h] compose up -d --no-deps --scale backend=$n backend frontend → wait /health/ready → next"
  done
  # 5. regenerate static Caddy upstream from app hosts + reload
  info "  [$(role_host lb)] ensure-cert.sh (self-signed placeholder if certs/ empty) → render static upstream ($(for i in $(app_indices); do printf '%s:8000 ' "${HOST[$i]}"; done)) → caddy reload"
  # 6. smoke through LB
  info "  smoke via LB ($(role_host lb))"
  echo
  info "UNEXERCISED SEAMS (must validate on the 2nd machine before un-gating):"
  info "  a) backend port publishing — base compose only 'expose: 8000' (compose-internal);"
  info "     cross-host LB needs it host-published. Add a fleet overlay, do NOT edit the"
  info "     single-host compose (keeps the tested dynamic-DNS path intact)."
  info "  b) static Caddy upstream — single-host uses 'dynamic a { name backend }' (docker"
  info "     DNS); cross-host has no shared DNS, needs a generated 'k1:8000 k2:8000' list."
  info "  c) per-host .env DB/Redis URLs must point at the infra host, not localhost."
}

# ════════════════════════════════════════════════════════════════════
# status
# ════════════════════════════════════════════════════════════════════
cmd_status() {
  resolve_sandbox_enablement
  parse_conf
  local cur; cur="$(state_get current)"
  step "fleet status${cur:+ (deployed version: $cur)}"
  local host
  while IFS= read -r host; do
    printf '\n  \033[1m[%s]\033[0m\n' "$host"
    local ps
    # No `--profile infra` needed: `docker compose ps` lists ALL running project
    # containers regardless of which profiles are active (verified on compose
    # v2), so pg/redis show up here even though they're profile-gated.
    ps="$(compose_on "$host" "${cur:-latest}" ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null)"
    if [[ -n "$ps" ]]; then printf '%s\n' "$ps" | sed 's/^/    /'; else info "no compose project up (or unreachable)"; fi
  done < <(all_hosts)
  # health via LB
  local lb; lb="$(role_host lb)"
  echo
  if run_on "$lb" "$(lb_ready_cmd)" 2>/dev/null; then ok "LB /health/ready green ($lb)"; else bad "LB /health/ready NOT green ($lb)"; fi
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
  step "rollback: $cur → $prev (images for $prev must still be loaded)"
  local hosts; hosts="$(all_hosts)"
  if [[ "$hosts" != "local" ]]; then
    info "multi-host rollback authored but UNEXERCISED — inspect and run per-host manually"
    (( DRY )) || die "multi-host rollback gated off until validated"
  fi
  local i n="" profile="" scale_arg=""
  for i in $(app_indices); do n="${SCALE[$i]}"; done
  has_infra && profile="--profile infra"
  [[ -n "$n" ]] && scale_arg="--scale backend=$n"
  if (( DRY )); then
    info "would: AF_VERSION=$prev docker compose -f $COMPOSE_FILE $([[ "$ENABLE_SANDBOX" == 1 ]] && printf '%s' "-f $SANDBOX_COMPOSE_FILE") $profile up -d --remove-orphans $scale_arg"
    return 0
  fi
  local compose_args=(-f "$COMPOSE_FILE")
  [[ "$ENABLE_SANDBOX" == 1 ]] && compose_args+=(-f "$SANDBOX_COMPOSE_FILE")
  # shellcheck disable=SC2086
  env AF_VERSION="$prev" docker compose "${compose_args[@]}" $profile up -d --remove-orphans $scale_arg \
    || die "rollback compose up failed"
  wait_ready local
  smoke local
  state_write "$prev" "$cur"   # swap: now-current is prev, and cur becomes the thing to re-forward to
  ok "rolled back to $prev"
}

# ── dispatch ────────────────────────────────────────────────────────
usage() { sed -n '2,40p' "$0"; }
main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    init-local) cmd_init_local "$@" ;;
    preflight) cmd_preflight "$@" ;;
    deploy)    cmd_deploy "$@" ;;
    prepare-sandbox) cmd_prepare_sandbox "$@" ;;
    status)    cmd_status "$@" ;;
    rollback)  cmd_rollback "$@" ;;
    ""|-h|--help|help) usage ;;
    *) die "unknown subcommand: $sub (try: init-local | preflight | deploy | prepare-sandbox | status | rollback)" ;;
  esac
}
main "$@"
