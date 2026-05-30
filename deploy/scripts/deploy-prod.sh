#!/usr/bin/env bash
# One-shot deploy/upgrade for the PUBLIC (Mode 2 / Caddy) deployment.
#
# This is the public-side counterpart to the intranet release.sh flow — but it's
# a thin wrapper, NOT a release builder. The intranet path needs release.sh
# because it's air-gapped (build → docker save → scp tar → docker load). A
# public host has network: it can pull base images and build app images locally,
# so "deploy" collapses to: get latest code → (build) → up → watch Caddy get its
# cert. No tarballs, no checksums.
#
# Usage:
#   deploy/scripts/deploy-prod.sh [--pull] [--build] [--no-cert-watch]
#
#   --pull           git pull --ff-only before deploying (default: skip; deploy
#                    whatever's in the working tree, so you can deploy local edits)
#   --build          docker compose build before up (default: skip; reuse existing
#                    images. Use after code changes that need a fresh image)
#   --no-cert-watch  don't tail caddy logs for the cert-acquisition line at the end
#
# For a graceful zero-surprise window (maintenance page during the swap), use
# pause-prod.sh / resume-prod.sh instead. This script is the blunt "just bring it
# up" path — fine for the first deploy and for low-traffic upgrades.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yml"
ENV_FILE="$ROOT/.env"

DO_PULL=0
DO_BUILD=0
CERT_WATCH=1
for arg in "$@"; do
  case "$arg" in
    --pull)          DO_PULL=1 ;;
    --build)         DO_BUILD=1 ;;
    --no-cert-watch) CERT_WATCH=0 ;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0 ;;
    *)               echo "Unknown arg: $arg (use -h for usage)" >&2; exit 2 ;;
  esac
done

# Pick docker compose CLI (V2 preferred, V1 fallback).
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "Error: neither 'docker compose' nor 'docker-compose' available" >&2
  exit 1
fi

# Preflight: .env must exist and carry the Caddy vars, else `up` fails late with
# a cryptic interpolation error (compose has `:?` on AF_DOMAIN/AF_ACME_EMAIL).
if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: $ENV_FILE not found. Copy deploy/.env.prod.example → .env and fill it." >&2
  exit 1
fi
missing=()
for var in AF_DOMAIN AF_ACME_EMAIL ARTIFACTFLOW_JWT_SECRET; do
  # Match `VAR=<non-empty>` ignoring leading spaces; tolerate quotes.
  if ! grep -qE "^[[:space:]]*${var}[[:space:]]*=[[:space:]]*[\"']?[^\"'[:space:]]" "$ENV_FILE"; then
    missing+=("$var")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "Error: these required vars are empty/missing in $ENV_FILE:" >&2
  printf '  - %s\n' "${missing[@]}" >&2
  echo "Fill them (see deploy/.env.prod.example) and re-run." >&2
  exit 1
fi

# Read AF_DOMAIN for the closing hint (literal value from .env, no sourcing).
AF_DOMAIN_VAL=$(awk -F= '/^[[:space:]]*AF_DOMAIN[[:space:]]*=/{v=$2; gsub(/^[[:space:]"'\'']+|[[:space:]"'\'']+$/,"",v); last=v} END{print last}' "$ENV_FILE")

if (( DO_PULL )); then
  echo "→ git pull --ff-only"
  git -C "$ROOT" pull --ff-only
fi

if (( DO_BUILD )); then
  echo "→ Building app images"
  "${DC[@]}" -f "$COMPOSE_FILE" build backend frontend
fi

echo "→ Bringing the stack up (--profile infra)"
"${DC[@]}" -f "$COMPOSE_FILE" --profile infra up -d

echo
echo "✓ Stack is up (Mode 2 / 公网)"
echo "  域名: https://${AF_DOMAIN_VAL:-<AF_DOMAIN>}"
echo "  健康: ${DC[*]} -f $COMPOSE_FILE exec caddy wget -qO- http://backend:8000/health/ready"

if (( CERT_WATCH )); then
  echo
  echo "→ Watching caddy logs for the TLS cert (Ctrl-C 退出，不影响服务)"
  echo "  期待看到: 'certificate obtained successfully' 或 'served key authentication'"
  echo "  若卡住: 确认 DNS 已解析到本机 + 防火墙开了 80/443 (ACME HTTP-01 需要 80)"
  echo
  "${DC[@]}" -f "$COMPOSE_FILE" logs -f --tail=30 caddy
fi
