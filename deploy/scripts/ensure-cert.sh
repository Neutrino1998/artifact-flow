#!/usr/bin/env bash
# ensure-cert.sh — guarantee deploy/certs/{server.crt,server.key} EXIST so the
# intranet Caddy (Mode 3, `tls <cert> <key>` + `auto_https off`) can boot.
#
# The intranet entry hard-references the two pem files; a missing file makes
# Caddy fail config load and the container never starts. On a fresh box the real
# cert (company test center / internal CA) often hasn't arrived yet. This script
# drops a SELF-SIGNED PLACEHOLDER so the stack comes up now; the operator later
# overwrites the two pems with the real chain and `caddy reload`s — zero downtime.
#
# It is IDEMPOTENT and NON-DESTRUCTIVE: if both files already exist and are
# non-empty it does nothing, so it can never clobber a real cert. Only Mode 3
# needs this — Mode 2 (public) uses ACME and mounts no certs dir.
#
# Usage:
#   deploy/scripts/ensure-cert.sh                 # placeholder for localhost
#   AF_CERT_HOSTS=af.corp.local,10.0.0.7 \
#     deploy/scripts/ensure-cert.sh               # add extra SAN entries
#
# Exit: 0 = cert present (pre-existing or freshly generated); 1 = openssl missing
# or generation failed.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${AF_CERTS_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)/certs}"
CRT="$CERTS_DIR/server.crt"
KEY="$CERTS_DIR/server.key"

# Already provisioned (real cert or an earlier placeholder) → never touch it.
if [[ -s "$CRT" && -s "$KEY" ]]; then
  echo "  ℹ cert present at $CERTS_DIR — leaving it untouched"
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "  ✗ no cert at $CERTS_DIR and openssl not on PATH — install openssl or" >&2
  echo "    drop a real server.crt/server.key there before starting Caddy" >&2
  exit 1
fi

mkdir -p "$CERTS_DIR"

# Build the SAN list: always cover localhost/loopback (the resume.sh probe and
# local smoke hit it); append any AF_CERT_HOSTS, routing bare IPs to IP: and
# everything else to DNS:. A self-signed cert is untrusted regardless of SAN, so
# this is about not tripping hostname-mismatch on top of the trust warning.
#
# "${extra[@]:-}" (not "${extra[@]}"): under `set -u`, bash < 4.4 treats an
# EMPTY array subscripted with [@] as an unbound-variable reference and exits —
# hits the default no-AF_CERT_HOSTS case on both plain macOS bash (3.2) and the
# CentOS 7 intranet target (4.2), i.e. exactly this script's own fresh-box
# scenario. The `:-` default (falls back to a single empty element, already
# absorbed by the `-z` check below) is set-u-safe on all bash versions.
san="DNS:localhost,IP:127.0.0.1"
IFS=',' read -ra extra <<<"${AF_CERT_HOSTS:-}"
for h in "${extra[@]:-}"; do
  h="${h// /}"; [[ -z "$h" ]] && continue
  if [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then san+=",IP:$h"; else san+=",DNS:$h"; fi
done

echo "  ⚠ no cert at $CERTS_DIR — generating a SELF-SIGNED PLACEHOLDER (SAN: $san)"
# `-addext` needs OpenSSL 1.1.1+ (missing on CentOS 7's stock 1.0.2k); a
# generated -config file with an x509_extensions section is the portable way
# to set SAN that works unchanged from OpenSSL 1.0.2 through 3.x / LibreSSL.
san_conf="$(mktemp)"
cat > "$san_conf" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no
[req_distinguished_name]
CN = artifactflow-selfsigned
[v3_req]
subjectAltName = $san
EOF
gen_ok=1
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$KEY" -out "$CRT" \
    -config "$san_conf" >/dev/null 2>&1 || gen_ok=0
rm -f "$san_conf"
if (( ! gen_ok )); then
  echo "  ✗ openssl failed to generate the placeholder cert" >&2
  rm -f "$CRT" "$KEY"   # don't leave a half-written pair Caddy would choke on
  exit 1
fi
chmod 600 "$KEY"

echo "  ✓ placeholder written — Caddy will boot, but clients see an UNTRUSTED cert."
echo "    Replace $CRT + $KEY with the real chain, then:"
echo "      docker compose -f deploy/docker-compose.intranet.yml exec caddy \\"
echo "        caddy reload --config /etc/caddy/conf/Caddyfile.intranet --adapter caddyfile"
exit 0
