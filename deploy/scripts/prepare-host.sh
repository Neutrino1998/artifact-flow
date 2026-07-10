#!/usr/bin/env bash
# prepare-host.sh — target-host readiness + optional sandbox bootstrap.
#
# Run on the target host from the deployment root after deploy/ has been
# extracted:
#
#   cd /opt/artifactflow
#   deploy/scripts/prepare-host.sh check
#   deploy/scripts/prepare-host.sh sandbox
#   deploy/scripts/prepare-host.sh all
#
# Env:
#   AF_VERSION                 expected app version tag (optional, for image check)
#   AF_CERT_HOSTS              comma-separated SANs for self-signed placeholder cert
#   AF_WITH_INFRA              set 1 when this host will run bundled Postgres/Redis
#   AF_SANDBOX_POOL_SIZE       fixed scratch pool size, default 8G starter
#   AF_SANDBOX_SCRATCH_ROOT    default /var/lib/artifactflow/sandbox-scratch
#   AF_SANDBOX_POOL            default /var/lib/artifactflow/sandbox-pool.img
#   AF_SANDBOX_IMAGE           default: newest artifactflow-sandbox-*.tar.gz in cwd
#   AF_SANDBOX_VERIFY          default: newest artifactflow-sandbox-verify-*.tar.gz in cwd
#   AF_GVISOR_PACKAGE          default: newest sandbox-gvisor-*.tar.gz in cwd

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; fail=$((fail + 1)); }
info() { printf '  \033[2mℹ %s\033[0m\n' "$1"; }
step() { printf '\033[1m▶ %s\033[0m\n' "$1"; }
die()  { bad "$1"; exit "${2:-1}"; }

need_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "must run as root on the target host"
}

newest() {
  local pattern="$1"
  find "$ROOT" -maxdepth 1 -type f -name "$pattern" -print 2>/dev/null | sort | tail -1
}

newest_sandbox_image() {
  find "$ROOT" -maxdepth 1 -type f -name 'artifactflow-sandbox-*.tar.gz' ! -name 'artifactflow-sandbox-verify-*.tar.gz' -print 2>/dev/null \
    | sort | tail -1
}

deploy_env_value() {
  local key="$1" env_file="$ROOT/deploy/.env" line value
  [[ -f "$env_file" ]] || return 1
  line="$(grep -E "^${key}=" "$env_file" | tail -1 || true)"
  [[ -n "$line" ]] || return 1
  value="${line#*=}"
  value="${value%$'\r'}"
  printf '%s\n' "$value"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "$value"
}

db_url_driver() {
  case "$1" in
    postgresql+asyncpg://*) echo "postgres" ;;
    mysql+aiomysql://*) echo "mysql" ;;
    sqlite+aiosqlite://*) echo "sqlite" ;;
    *) echo "" ;;
  esac
}

db_url_uses_bundled_postgres() {
  local urls="$1" raw url
  local -a raw_urls=()
  IFS=',' read -ra raw_urls <<< "$urls"
  for raw in "${raw_urls[@]}"; do
    url="$(trim "$raw")"
    [[ "$url" == *@postgres:* || "$url" == *@postgres/* ]] && return 0
  done
  return 1
}

check_db_urls() {
  local urls="$1" source="$2" raw url driver first_driver="" count=0
  local -a raw_urls=()
  IFS=',' read -ra raw_urls <<< "$urls"
  for raw in "${raw_urls[@]}"; do
    url="$(trim "$raw")"
    [[ -z "$url" ]] && continue
    count=$((count + 1))
    if [[ "$url" == *CHANGE_ME* ]]; then
      bad "deploy/.env $source still contains CHANGE_ME"
      continue
    fi
    driver="$(db_url_driver "$url")"
    case "$driver" in
      postgres|mysql)
        if [[ -z "$first_driver" ]]; then
          first_driver="$driver"
        elif [[ "$driver" != "$first_driver" ]]; then
          bad "deploy/.env $source mixes database drivers; keep all URLs PostgreSQL or all MySQL/TDSQL"
        fi
        ;;
      sqlite)
        info "deploy/.env $source uses SQLite; OK for dev/test, not recommended for intranet deployment"
        ;;
      *)
        bad "deploy/.env $source has unsupported database URL driver: $url"
        ;;
    esac
  done
  (( count > 0 )) || bad "deploy/.env $source is empty"
}

check_postgres_infra_keys() {
  local key value missing=0
  for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    value="$(deploy_env_value "$key" || true)"
    if [[ -z "$value" ]]; then
      bad "deploy/.env bundled Postgres value missing or empty: $key"
      missing=1
    elif [[ "$value" == *CHANGE_ME* ]]; then
      bad "deploy/.env bundled Postgres value still contains CHANGE_ME: $key"
      missing=1
    fi
  done
  (( missing == 0 )) && ok "deploy/.env bundled Postgres values are set"
}

validate_fernet_key() {
  local key="$1"
  command -v python3 >/dev/null 2>&1 || return 2
  python3 - "$key" <<'PY' >/dev/null 2>&1
import base64
import sys

try:
    raw = base64.urlsafe_b64decode(sys.argv[1].encode())
except Exception:
    sys.exit(1)
sys.exit(0 if len(raw) == 32 else 1)
PY
}

warn_unused_postgres_placeholders() {
  local key value
  for key in POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD; do
    value="$(deploy_env_value "$key" || true)"
    if [[ -z "$value" ]]; then
      continue
    elif [[ "$value" == *CHANGE_ME* ]]; then
      info "$key still contains CHANGE_ME; ignored unless you deploy with --profile infra"
    fi
  done
}

sandbox_scratch_root() {
  local root=""
  root="$(deploy_env_value ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT || true)"
  root="${root:-${AF_SANDBOX_SCRATCH_ROOT:-/var/lib/artifactflow/sandbox-scratch}}"
  printf '%s\n' "$root"
}

verify_adjacent_sha() {
  local file="$1"
  local sha="$file.sha256"
  [[ -f "$sha" ]] || { echo "missing checksum file: $sha" >&2; return 1; }
  ( cd "$(dirname "$file")" && sha256sum -c "$(basename "$sha")" )
}

compose_files() {
  printf '%s\n' -f "$ROOT/deploy/docker-compose.intranet.yml"
  if [[ "${AF_ENABLE_SANDBOX:-0}" == 1 ]]; then
    printf '%s\n' -f "$ROOT/deploy/docker-compose.sandbox.yml"
  fi
}

cmd_check() {
  step "host preflight"

  command -v docker >/dev/null 2>&1 && ok "docker present" || bad "docker missing"
  docker compose version >/dev/null 2>&1 && ok "docker compose v2 present" || bad "docker compose v2 missing"
  docker info >/dev/null 2>&1 && ok "docker daemon reachable" || bad "docker daemon unreachable"
  command -v openssl >/dev/null 2>&1 && ok "openssl present" || bad "openssl missing (needed for placeholder cert)"

  local arch; arch="$(uname -m 2>/dev/null || true)"
  case "$arch" in
    x86_64|aarch64|arm64) ok "arch supported: $arch" ;;
    *) bad "unsupported/unknown arch: ${arch:-<empty>}" ;;
  esac

  local avail_kb
  avail_kb="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR==2{print $4}')"
  if [[ -n "$avail_kb" && "$avail_kb" -ge 5242880 ]]; then
    ok "disk free >= 5GiB on deployment filesystem"
  else
    bad "disk free < 5GiB or unreadable for $ROOT"
  fi

  if free -h >/dev/null 2>&1; then
    info "$(free -h | awk '/^Mem:/{print "memory total="$2", available="$7} /^Swap:/{print "swap total="$2", free="$4}')"
  fi

  if ss -lnt 2>/dev/null | awk '$4 ~ /:80$/ || $4 ~ /:443$/ {found=1} END{exit found?0:1}'; then
    info "80/443 already have listeners; OK if they are this deployment's Caddy, otherwise stop the conflict first"
  else
    ok "80/443 appear free before first start"
  fi

  step "bundle files"
  if compgen -G "$ROOT/artifactflow-*.tar.gz.sha256" >/dev/null; then
    ( cd "$ROOT" && sha256sum -c artifactflow-*.tar.gz.sha256 ) && ok "artifactflow bundle checksums OK" || bad "artifactflow checksum verification failed"
  else
    info "no artifactflow *.sha256 files found in $ROOT"
  fi

  [[ -d "$ROOT/deploy" ]] && ok "deploy/ extracted" || bad "deploy/ missing"
  [[ -d "$ROOT/config" ]] && ok "config/ extracted" || bad "config/ missing"
  [[ -f "$ROOT/deploy/.env" ]] && ok "deploy/.env present" || bad "deploy/.env missing"

  if [[ -f "$ROOT/deploy/.env" ]]; then
    local key value missing=0
    local required_keys=(
      ARTIFACTFLOW_JWT_SECRET
      ARTIFACTFLOW_CREDENTIAL_KEY
      ARTIFACTFLOW_REDIS_URL
      ARTIFACTFLOW_REDIS_KEY_PREFIX
    )
    for key in "${required_keys[@]}"; do
      value="$(deploy_env_value "$key" || true)"
      if [[ -z "$value" ]]; then
        bad "deploy/.env required value missing or empty: $key"
        missing=1
      elif [[ "$value" == *CHANGE_ME* ]]; then
        bad "deploy/.env required value still contains CHANGE_ME: $key"
        missing=1
      elif [[ "$key" == "ARTIFACTFLOW_CREDENTIAL_KEY" ]]; then
        if validate_fernet_key "$value"; then
          ok "deploy/.env ARTIFACTFLOW_CREDENTIAL_KEY is a valid Fernet key"
        else
          case "$?" in
            2) info "python3 not found; skipping Fernet key format check" ;;
            *) bad "deploy/.env ARTIFACTFLOW_CREDENTIAL_KEY is not a valid Fernet key" ;;
          esac
        fi
      fi
    done
    (( missing == 0 )) && ok "deploy/.env common required values are set"

    local db_urls db_url effective_db_urls
    db_urls="$(deploy_env_value ARTIFACTFLOW_DATABASE_URLS || true)"
    db_url="$(deploy_env_value ARTIFACTFLOW_DATABASE_URL || true)"
    effective_db_urls="${db_urls:-$db_url}"
    if [[ -z "$effective_db_urls" ]]; then
      bad "deploy/.env requires ARTIFACTFLOW_DATABASE_URL or ARTIFACTFLOW_DATABASE_URLS"
    else
      check_db_urls "$effective_db_urls" "$([[ -n "$db_urls" ]] && printf ARTIFACTFLOW_DATABASE_URLS || printf ARTIFACTFLOW_DATABASE_URL)"
    fi

    if [[ "${AF_WITH_INFRA:-0}" == 1 ]] || db_url_uses_bundled_postgres "$effective_db_urls"; then
      check_postgres_infra_keys
    else
      warn_unused_postgres_placeholders
    fi

    local optional_blanks
    optional_blanks="$(
      grep -n '^[A-Z0-9_][A-Z0-9_]*=$' "$ROOT/deploy/.env" \
        | grep -Ev '^[0-9]+:(ARTIFACTFLOW_JWT_SECRET|ARTIFACTFLOW_CREDENTIAL_KEY|ARTIFACTFLOW_REDIS_URL|ARTIFACTFLOW_REDIS_KEY_PREFIX)=$' \
        || true
    )"
    if [[ -n "$optional_blanks" ]]; then
      info "optional empty KEY= lines present (OK if unused):"
      printf '%s\n' "$optional_blanks" | sed 's/^/      /'
    fi
  fi

  step "TLS placeholder"
  if [[ -x "$ROOT/deploy/scripts/ensure-cert.sh" ]]; then
    AF_CERTS_DIR="$ROOT/deploy/certs" AF_CERT_HOSTS="${AF_CERT_HOSTS:-}" "$ROOT/deploy/scripts/ensure-cert.sh" \
      && ok "certificate files present" || bad "certificate bootstrap failed"
  else
    bad "deploy/scripts/ensure-cert.sh missing or not executable"
  fi

  step "docker images"
  if [[ -n "${AF_VERSION:-}" ]]; then
    docker image inspect "artifactflow:${AF_VERSION}" >/dev/null 2>&1 && ok "artifactflow:${AF_VERSION} loaded" || info "artifactflow:${AF_VERSION} not loaded yet"
    docker image inspect "artifactflow-frontend:${AF_VERSION}" >/dev/null 2>&1 && ok "artifactflow-frontend:${AF_VERSION} loaded" || info "artifactflow-frontend:${AF_VERSION} not loaded yet"
  fi

  if [[ "${AF_ENABLE_SANDBOX:-0}" == 1 ]]; then
    step "sandbox preflight"
    command -v runsc >/dev/null 2>&1 && ok "runsc present" || bad "runsc missing; run: deploy/scripts/prepare-host.sh sandbox"
    docker info 2>/dev/null | grep -q runsc && ok "docker runtime runsc registered" || bad "docker runtime runsc not registered"
    local scratch
    scratch="$(sandbox_scratch_root)"
    findmnt -rn "$scratch" >/dev/null \
      && ok "sandbox scratch root mounted: $scratch" || bad "sandbox scratch root not mounted: $scratch"
    docker image inspect artifactflow-sandbox:latest >/dev/null 2>&1 && ok "artifactflow-sandbox:latest loaded" || bad "artifactflow-sandbox:latest not loaded"
  fi

  echo
  (( fail == 0 )) && ok "prepare-host check OK" || die "prepare-host check found $fail blocker(s)"
}

cmd_sandbox() {
  need_root
  step "sandbox bootstrap"

  local gvisor image verify
  gvisor="${AF_GVISOR_PACKAGE:-$(newest 'sandbox-gvisor-*.tar.gz')}"
  image="${AF_SANDBOX_IMAGE:-$(newest_sandbox_image)}"
  verify="${AF_SANDBOX_VERIFY:-$(newest 'artifactflow-sandbox-verify-*.tar.gz')}"

  [[ -n "$gvisor" && -f "$gvisor" ]] || die "gVisor package not found (expected sandbox-gvisor-*.tar.gz or AF_GVISOR_PACKAGE)"
  [[ -n "$image" && -f "$image" ]] || die "sandbox image tar not found (expected artifactflow-sandbox-*.tar.gz or AF_SANDBOX_IMAGE)"
  [[ -n "$verify" && -f "$verify" ]] || die "sandbox verify tar not found (expected artifactflow-sandbox-verify-*.tar.gz or AF_SANDBOX_VERIFY)"

  if [[ -f "$gvisor.sha256" || -f "$image.sha256" || -f "$verify.sha256" ]]; then
    step "verify sandbox transfer units"
    verify_adjacent_sha "$gvisor" && verify_adjacent_sha "$image" && verify_adjacent_sha "$verify" \
      && ok "sandbox checksums OK" || die "sandbox checksum verification failed"
  fi

  local gvisor_dir
  gvisor_dir="$ROOT/$(basename "$gvisor" .tar.gz)"
  if [[ ! -d "$gvisor_dir" ]]; then
    tar xzf "$gvisor" -C "$ROOT" || die "failed to extract $gvisor"
  fi
  [[ -x "$gvisor_dir/install.sh" ]] || die "missing $gvisor_dir/install.sh"

  "$gvisor_dir/install.sh" || die "gVisor install failed"
  systemctl reload docker || die "docker reload failed"

  local load_output loaded_tag loaded_latest
  load_output="$(docker load -i "$image")" || die "sandbox docker load failed"
  printf '%s\n' "$load_output" | sed 's/^/  /'
  loaded_latest="$(printf '%s\n' "$load_output" | awk '$0 == "Loaded image: artifactflow-sandbox:latest" {print; exit}')"
  loaded_tag="$(printf '%s\n' "$load_output" | awk -F': ' '/^Loaded image: artifactflow-sandbox:/ && $2 !~ /:latest$/ {print $2; exit}')"
  if [[ -n "$loaded_latest" ]]; then
    ok "artifactflow-sandbox:latest loaded from $(basename "$image")"
  elif [[ -n "$loaded_tag" ]]; then
    docker tag "$loaded_tag" artifactflow-sandbox:latest || die "failed to tag $loaded_tag as artifactflow-sandbox:latest"
    ok "tagged $loaded_tag as artifactflow-sandbox:latest"
  else
    die "loaded sandbox image tag not found in docker load output"
  fi

  "$gvisor_dir/smoke-test.sh" artifactflow-sandbox:latest || die "gVisor smoke failed"

  local pool root size
  pool="${AF_SANDBOX_POOL:-/var/lib/artifactflow/sandbox-pool.img}"
  root="${AF_SANDBOX_SCRATCH_ROOT:-$(sandbox_scratch_root)}"
  size="${AF_SANDBOX_POOL_SIZE:-8G}"

  step "sandbox scratch loop filesystem ($size at $root)"
  mkdir -p "$(dirname "$pool")" "$root"
  if findmnt -rn "$root" >/dev/null; then
    ok "$root already mounted"
  elif [[ -e "$pool" ]]; then
    die "$pool exists but $root is not mounted; inspect manually before continuing"
  else
    fallocate -l "$size" "$pool" || die "fallocate $pool failed"
    mkfs.ext4 -m 0 -F "$pool" || die "mkfs.ext4 $pool failed"
    grep -q "^$pool $root " /etc/fstab || printf '%s %s ext4 loop,nosuid,nodev 0 0\n' "$pool" "$root" >> /etc/fstab
    mount "$root" || die "mount $root failed"
    ok "$root mounted"
  fi
  df -h "$root" | sed 's/^/  /'

  step "sandbox verify probes"
  tar xzf "$verify" -C "$ROOT" || die "failed to extract $verify"
  ( cd "$ROOT" && IMAGE=artifactflow-sandbox:latest bash verify/run-all.sh ) || die "sandbox verify failed"

  if [[ -f "$ROOT/deploy/.env" ]]; then
    step "write deploy/.env sandbox settings"
    if grep -q '^ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=' "$ROOT/deploy/.env"; then
      sed -i "s|^ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=.*|ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=$root|" "$ROOT/deploy/.env"
    else
      printf '\nARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=%s\n' "$root" >> "$ROOT/deploy/.env"
    fi
    if grep -q '^ARTIFACTFLOW_SANDBOX_RUNTIME=' "$ROOT/deploy/.env"; then
      sed -i 's|^ARTIFACTFLOW_SANDBOX_RUNTIME=.*|ARTIFACTFLOW_SANDBOX_RUNTIME=runsc|' "$ROOT/deploy/.env"
    else
      printf 'ARTIFACTFLOW_SANDBOX_RUNTIME=runsc\n' >> "$ROOT/deploy/.env"
    fi
    if ! grep -q '^ARTIFACTFLOW_SANDBOX_MEM_LIMIT_MB=' "$ROOT/deploy/.env"; then
      printf 'ARTIFACTFLOW_SANDBOX_MEM_LIMIT_MB=1024\n' >> "$ROOT/deploy/.env"
    fi
    ok "deploy/.env sandbox settings present"
  else
    info "deploy/.env missing; create it, then add ARTIFACTFLOW_SANDBOX_SCRATCH_ROOT=$root"
  fi

  ok "sandbox bootstrap OK"
}

usage() {
  sed -n '2,24p' "$0"
}

main() {
  local sub="${1:-check}"
  case "$sub" in
    check) cmd_check ;;
    sandbox) cmd_sandbox ;;
    all) cmd_check; cmd_sandbox; AF_ENABLE_SANDBOX=1 cmd_check ;;
    -h|--help|help) usage ;;
    *) die "unknown subcommand: $sub (try: check | sandbox | all)" ;;
  esac
}

main "$@"
