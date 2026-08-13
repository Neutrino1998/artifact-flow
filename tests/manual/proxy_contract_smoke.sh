#!/usr/bin/env bash
# proxy_contract_smoke.sh — regression battery for the Caddy reverse-proxy
# contract. Runs the real caddy container against a stub upstream
# (proxy_stub.py) and asserts the behaviours that must survive any edit to
# deploy/caddy/common.caddy: routing ownership, Swagger block, X-Real-IP
# overwrite, upload cap + product error, SSE flush, maintenance gate. Run it
# after touching the caddy configs.
#
# History: born as the nginx⇆Caddy equivalence gate for the Mode 3 migration
# (one battery, two targets). nginx retired 2026-07 (commit d489c0e) — the
# nginx half is gone; the battery itself is what still earns its keep.
#
# Target configs: the exact static-TLS entry exercises the full application
# contract over real HTTPS plus its HTTP→HTTPS redirect. A second focused target
# runs the exact ACME entry with Caddy's localhost CA, proving that its explicit
# redirect and HTTPS responses share the same technology-header policy without
# contacting a public CA.
#
# Usage:
#   tests/manual/proxy_contract_smoke.sh
#
# Env knobs:
#   SMOKE_HTTP_PORT   host port for HTTP redirects (default 18080)
#   SMOKE_HTTPS_PORT  host port for the HTTPS application (default 18443)
#   SMOKE_SSE         SSE buffering check: fail | warn | skip   (default fail)
#   SMOKE_SKIP_UPLOAD set non-empty to skip the 211MB upload-cap check (slowest)
#
# Requires: docker, curl, openssl, python3 on the host. No project services needed.
# NOT collected by pytest (no test_ prefix, lives in tests/manual/).
#
# Contract checked (mirror of deploy/caddy/common.caddy):
#   1. Swagger 404                          5. maintenance gate → 503 for /, /api, SSE
#   2. /health ungated AND routed to backend   6. maintenance assets + note reachable mid-window
#   3. routing (api & health→8000, /→3000)  7. upload: 211MiB→semantic JSON 413, 200MiB→backend 200
#   4. X-Real-IP injected + anti-spoof       8. SSE buffering off (incremental flush)
#   9. HTTPS + both HTTP 308 entries strip technology-identifying headers

set -uo pipefail  # NOT -e: run every assertion, tally at the end

HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
HTTPS_PORT="${SMOKE_HTTPS_PORT:-18443}"
SMOKE_SSE="${SMOKE_SSE:-fail}"
BASE="https://localhost:${HTTPS_PORT}"
HTTP_BASE="http://localhost:${HTTP_PORT}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STUB_PY="$SCRIPT_DIR/proxy_stub.py"
CADDY_CONF_DIR="$ROOT/deploy/caddy"

NET="af-proxy-smoke-net"
STUB_CTR="af-proxy-smoke-stub"
PROXY_CTR="af-proxy-smoke-proxy"
MAINT_BASE="$(mktemp -d)"   # per-target copies of deploy/maintenance live here
CERT_DIR="$MAINT_BASE/certs"
TMP_MAINT=""                # set per target by run_target
FLAG=""
HOST_HEADER="test.local"

PASS=0; FAIL=0
declare -a FAILED_TARGETS

# The static-TLS site is host-agnostic, so host-side curl sends Host: test.local
# while connecting to localhost. -k trusts only this test's ephemeral cert.
# --noproxy '*' is mandatory: a dev box with http_proxy/all_proxy set (Clash etc.)
# would otherwise route localhost through the proxy, which 502s the published port
# before the request ever reaches the container.
hc() { curl -k -s -m 10 --noproxy '*' -H "Host: $HOST_HEADER" "$@"; }
code_of() { hc -o /dev/null -w "%{http_code}" "$@"; }
# served_by_port from the stub's JSON echo — proves WHICH upstream served the
# path (8000 backend / 3000 frontend), not just that something returned 200.
# Empty if the response isn't the stub's JSON (e.g. the 503 maintenance page).
port_of() { hc "$BASE$1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('served_by_port',''))" 2>/dev/null; }

ok()   { PASS=$((PASS+1)); echo "  ✓ $1"; }
no()   { FAIL=$((FAIL+1)); echo "  ✗ $1"; }

# Poll $1 until it returns code $2, up to ~3s. The maintenance flag is a
# per-request file stat, but a bind-mount (esp. OrbStack/virtiofs) can lag a few
# hundred ms reflecting a host-side create/delete — that latency is a test
# artifact, not a contract divergence, so tolerate it instead of single-shotting.
wait_code() {
  local i c=""
  for i in $(seq 1 15); do
    c="$(code_of "$BASE$1")"
    [[ "$c" == "$2" ]] && return 0
    sleep 0.2
  done
  return 1
}

cleanup() {
  docker rm -f "$PROXY_CTR" "$STUB_CTR" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$MAINT_BASE"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
preflight() {
  command -v docker >/dev/null || { echo "docker 不可用"; exit 2; }
  command -v openssl >/dev/null || { echo "openssl 不可用"; exit 2; }
  command -v python3 >/dev/null || { echo "python3 不可用"; exit 2; }
  [[ -f "$STUB_PY" ]]                       || { echo "缺 $STUB_PY"; exit 2; }
  [[ -f "$CADDY_CONF_DIR/Caddyfile.acme" ]] || { echo "缺 $CADDY_CONF_DIR/Caddyfile.acme"; exit 2; }
  [[ -f "$CADDY_CONF_DIR/Caddyfile.static" ]] || { echo "缺 $CADDY_CONF_DIR/Caddyfile.static"; exit 2; }
  [[ -f "$CADDY_CONF_DIR/common.caddy" ]]   || { echo "缺 $CADDY_CONF_DIR/common.caddy"; exit 2; }

  mkdir -p "$CERT_DIR"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
    -subj "/CN=test.local" \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
    >/dev/null 2>&1
}

start_stub() {
  docker network create "$NET" >/dev/null
  docker run -d --name "$STUB_CTR" --network "$NET" \
    --network-alias backend --network-alias frontend \
    -v "$STUB_PY":/app/proxy_stub.py:ro \
    python:3.11-slim python /app/proxy_stub.py >/dev/null
  # Wait until both upstream ports listen before starting the proxy, so the
  # first assertions don't race the stub's boot.
  local i
  for i in $(seq 1 30); do
    if docker exec "$STUB_CTR" python -c \
      "import socket;[socket.create_connection(('127.0.0.1',p),1) for p in (8000,3000)]" \
      >/dev/null 2>&1; then return 0; fi
    sleep 0.3
  done
  echo "stub 未就绪"; docker logs "$STUB_CTR" 2>&1 | tail; exit 2
}

start_proxy() {
  docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
  # The exact static-TLS entry and both published ports mirror compose. Caddy's
  # directory mount + explicit --config also mirror compose (a single-file
  # mount would pin the inode AND break the common.caddy import). This test's
  # temp dir combines maintenance assets + flag, so mount it at both production
  # target paths: immutable page content and target-local mutable state.
  # Tag kept in lockstep with release.sh CADDY_TAG / both compose files.
  docker run -d --name "$PROXY_CTR" --network "$NET" \
    -p "$HTTP_PORT:80" -p "$HTTPS_PORT:443" \
    -e AF_HTTPS_PORT="$HTTPS_PORT" \
    -v "$CADDY_CONF_DIR":/etc/caddy/conf:ro \
    -v "$CERT_DIR":/etc/caddy/certs:ro \
    -v "$TMP_MAINT":/etc/caddy/maintenance:ro \
    -v "$TMP_MAINT":/etc/caddy/maintenance-state:ro \
    caddy:2.10-alpine caddy run --config /etc/caddy/conf/Caddyfile.static --adapter caddyfile >/dev/null
}

start_acme_proxy() {
  docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
  # localhost makes the exact ACME entry use Caddy's local issuer, exercising
  # automatic certificate management without contacting a public CA.
  docker run -d --name "$PROXY_CTR" --network "$NET" \
    -p "$HTTP_PORT:80" -p "$HTTPS_PORT:443" \
    -e AF_DOMAIN=localhost -e AF_ACME_EMAIL=smoke@test.local \
    -v "$CADDY_CONF_DIR":/etc/caddy/conf:ro \
    -v "$TMP_MAINT":/etc/caddy/maintenance:ro \
    -v "$TMP_MAINT":/etc/caddy/maintenance-state:ro \
    caddy:2.10-alpine caddy run --config /etc/caddy/conf/Caddyfile.acme --adapter caddyfile >/dev/null
}

wait_ready() {
  local i
  for i in $(seq 1 40); do
    [[ "$(code_of "$BASE/health/ready")" == "200" ]] && return 0
    sleep 0.5
  done
  echo "  ! 代理未就绪，最后 20 行日志:"; docker logs "$PROXY_CTR" 2>&1 | tail -20
  return 1
}

# ---------------------------------------------------------------------------
assert_routing() {
  local p
  p="$(port_of /api/ping)"
  [[ "$p" == "8000" ]] && ok "routing: /api/* → backend:8000" || no "routing: /api/* 应到 8000，实得 '$p'"
  # /health must reach backend, not merely return 200 — the stub answers 200 on
  # BOTH ports, so a /health misrouted to frontend would otherwise pass silently.
  p="$(port_of /health/ready)"
  [[ "$p" == "8000" ]] && ok "routing: /health/* → backend:8000" || no "routing: /health/* 应到 8000，实得 '$p'"
  p="$(port_of /)"
  [[ "$p" == "3000" ]] && ok "routing: / → frontend:3000" || no "routing: / 应到 3000，实得 '$p'"
}

assert_swagger_404() {
  local p bad=0
  for p in /docs /redoc /openapi.json; do
    [[ "$(code_of "$BASE$p")" == "404" ]] || { bad=1; no "swagger: $p 应 404"; }
  done
  [[ $bad == 0 ]] && ok "swagger /docs /redoc /openapi.json 均 404"
}

assert_real_ip() {
  # Send a spoofed X-Real-IP; the proxy must OVERWRITE it with the real peer IP
  # (backend's login throttle trusts only this header).
  local body xri
  body="$(hc -H "X-Real-IP: 9.9.9.9" "$BASE/api/whoami")"
  xri="$(echo "$body" | python3 -c "import sys,json;print(json.load(sys.stdin)['headers'].get('x-real-ip',''))" 2>/dev/null)"
  if [[ -z "$xri" ]]; then
    no "X-Real-IP: 未注入"
  elif [[ "$xri" == "9.9.9.9" ]]; then
    no "X-Real-IP: 透传了伪造值(应被覆盖) → $xri"
  elif [[ "$xri" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    ok "X-Real-IP: 注入真实 peer IP 且覆盖伪造值 ($xri)"
  else
    no "X-Real-IP: 值不像 IP → $xri"
  fi
}

assert_no_technology_headers() {
  local path headers leaked=0
  for path in / /api/ping /health/ready; do
    headers="$(hc -D - -o /dev/null "$BASE$path")"
    if printf '%s\n' "$headers" | grep -Eiq '^(x-powered-by|via|server)[[:space:]]*:'; then
      leaked=1
      no "technology headers: $path 仍暴露 X-Powered-By/Via/Server"
    fi
  done
  [[ $leaked -eq 0 ]] \
    && ok "technology headers: frontend/API/health 均未暴露 X-Powered-By/Via/Server"
}

assert_redirect() {
  local label="$1" expected_location="$2" headers status location
  headers="$(curl -sS --noproxy '*' -D - -o /dev/null "$HTTP_BASE/redirect-check?q=1")"
  status="$(printf '%s\n' "$headers" | sed -n '1s/\r$//p')"
  location="$(printf '%s\n' "$headers" | grep -i '^location:' | sed 's/^[^:]*:[[:space:]]*//;s/\r$//')"
  if [[ "$status" == *" 308 "* && "$location" == "$expected_location" ]]; then
    ok "$label: HTTP → HTTPS 308 且 Location 正确"
  else
    no "$label: 期望 308/正确 Location，实得 '$status' '$location'"
  fi
  if printf '%s\n' "$headers" | grep -Eiq '^(x-powered-by|via|server)[[:space:]]*:'; then
    no "$label: 308 仍暴露 X-Powered-By/Via/Server"
  else
    ok "$label: 308 未暴露 X-Powered-By/Via/Server"
  fi
}

assert_maintenance() {
  # Runs LAST, on a fresh per-target dir. Flag currently ABSENT → verify / proxies
  # (the flag-off state). Then CREATE the flag and verify the gate. We never
  # delete it: host→container creation propagates everywhere, host-side deletion
  # does NOT on OrbStack/virtiofs — and both flag states are covered anyway
  # (absent now, present after create).
  [[ "$(code_of "$BASE/")" == "200" ]] \
    && ok "维护门: flag 关 → / 代理正常(200)" \
    || no "维护门: flag 关时 / 应 200"
  # Flag ON → 503 maintenance page.
  : > "$FLAG"
  if wait_code / 503; then
    hc "$BASE/" | grep -q "系统维护中" \
      && ok "维护门: flag 开 → / 返回 503 维护页" \
      || no "维护门: flag 开 503 但响应非维护页"
  else
    no "维护门: flag 开后 / 未变 503"
  fi
  # Mid-window the gate also catches API + SSE (only /health and /__maintenance
  # bypass it). Without these, a drift to "frontend gated but API/SSE still live"
  # would pass.
  [[ "$(code_of "$BASE/api/ping")" == "503" ]] \
    && ok "维护门: /api/* 窗口内 → 503" \
    || no "维护门: /api/* 窗口内应 503"
  [[ "$(code_of "$BASE/api/v1/stream/x")" == "503" ]] \
    && ok "维护门: /api/v1/stream/* 窗口内 → 503" \
    || no "维护门: /api/v1/stream/* 窗口内应 503"
  # /health stays ungated AND correctly routed to backend (not just any 200).
  [[ "$(port_of /health/ready)" == "8000" ]] \
    && ok "维护门: /health 不被拦且仍达 backend:8000" \
    || no "维护门: /health 窗口内应达 backend:8000"
  [[ "$(code_of "$BASE/__maintenance/cat-sleep-dark.svg")" == "200" ]] \
    && ok "维护门: /__maintenance/* 资产窗口内可达" \
    || no "维护门: /__maintenance/* 窗口内应 200"
  if [[ "$(code_of "$BASE/__maintenance/note.txt")" == "200" ]] \
      && [[ "$(hc "$BASE/__maintenance/note.txt")" == "proxy-contract-note" ]]; then
    ok "维护门: operator note 窗口内可读取"
  else
    no "维护门: /__maintenance/note.txt 应返回 operator note"
  fi
}

assert_upload_cap() {
  if [[ -n "${SMOKE_SKIP_UPLOAD:-}" ]]; then
    echo "  ⊘ upload-cap: 跳过 (SMOKE_SKIP_UPLOAD)"; return
  fi
  local big code resp port body_file headers_file detail content_type
  # Negative: 211MiB (> the 210MiB cap) → proxy 413s before the body reaches
  # backend, with a product-owned JSON explanation rather than an empty/proxy-
  # identifying response. Sparse file → ~0 disk; curl streams the zeros over
  # loopback.
  big="$(mktemp)"
  body_file="$(mktemp)"
  headers_file="$(mktemp)"
  dd if=/dev/null of="$big" bs=1 count=0 seek=$((211*1024*1024)) 2>/dev/null
  code="$(curl -k -s -m 30 --noproxy '*' -H "Host: test.local" -H "Expect:" \
            -D "$headers_file" -o "$body_file" -w "%{http_code}" \
            -H "Content-Type: application/octet-stream" \
            --data-binary @"$big" "$BASE/api/upload")"
  detail="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("detail", ""))' "$body_file" 2>/dev/null)"
  content_type="$(grep -i '^content-type:' "$headers_file" | tail -1 | sed 's/^[^:]*:[[:space:]]*//;s/\r$//')"
  [[ "$code" == "413" ]] \
    && ok "upload-cap: 211MiB(超限) → 413" \
    || no "upload-cap: 211MiB 应 413，实得 '$code'"
  [[ "$detail" == "单次上传内容过大：所有文件合计不得超过 200MB。请减少文件数量或文件大小后重试。" ]] \
    && ok "upload-cap: 413 返回带 200MB 上限的用户可读 detail" \
    || no "upload-cap: 413 detail 缺失或不正确，实得 '$detail'"
  [[ "$content_type" == application/json* ]] \
    && ok "upload-cap: 413 声明 application/json" \
    || no "upload-cap: 413 Content-Type 应为 application/json，实得 '$content_type'"
  if grep -Eiq '^(x-powered-by|via|server)[[:space:]]*:' "$headers_file"; then
    no "upload-cap: 413 暴露了 X-Powered-By/Via/Server"
  else
    ok "upload-cap: 413 未暴露技术标识头"
  fi
  rm -f "$big" "$body_file" "$headers_file"
  # Positive: a max legit batch (200MiB = the authoritative total-bytes cap; note
  # this is now LESS than per-file×count = 200MB×30, by design) must pass the proxy
  # and REACH backend. Guards against the cap being silently lowered below the
  # valid-batch ceiling (e.g. to 100MB), which would 413 legitimate uploads while
  # the negative check above still passes. -w prints code on its own trailing
  # line; the body is the stub's small JSON echo (NOT the 200MiB upload).
  big="$(mktemp)"
  dd if=/dev/null of="$big" bs=1 count=0 seek=$((200*1024*1024)) 2>/dev/null
  resp="$(curl -k -s -m 30 --noproxy '*' -H "Host: test.local" -w $'\n%{http_code}' \
            -H "Content-Type: application/octet-stream" \
            --data-binary @"$big" "$BASE/api/upload")"
  rm -f "$big"
  code="${resp##*$'\n'}"
  port="$(printf '%s' "${resp%$'\n'*}" | python3 -c "import sys,json;print(json.load(sys.stdin).get('served_by_port',''))" 2>/dev/null)"
  if [[ "$code" == "200" && "$port" == "8000" ]]; then
    ok "upload-cap: 200MiB(合法批量) → 透传至 backend:8000(200)"
  else
    no "upload-cap: 200MiB 合法批量应透传 backend(200/8000)，实得 code=$code port=$port"
  fi
}

assert_sse() {
  if [[ "$SMOKE_SSE" == skip ]]; then
    echo "  ⊘ SSE: 跳过 (SMOKE_SSE=skip)"; return
  fi
  local gap rc
  # Read the stream byte-by-byte, timestamp chunk1 vs chunk2. Buffering-off →
  # gap ≈ stub's 1.5s sleep; buffering-on → both land together (gap ≈ 0).
  gap="$(python3 - "$HTTPS_PORT" <<'PY'
import http.client, ssl, sys, time
port = int(sys.argv[1])
c = http.client.HTTPSConnection(
    "localhost",
    port,
    timeout=15,
    context=ssl._create_unverified_context(),
)
c.putrequest("GET", "/api/v1/stream/test", skip_host=True, skip_accept_encoding=True)
c.putheader("Host", "test.local")
c.endheaders()
r = c.getresponse()
seen, buf = {}, b""
while len(seen) < 2:
    b = r.read(1)
    if not b:
        break
    buf += b
    if buf.endswith(b"\n\n"):
        line = buf.decode("utf-8", "replace")
        if "chunk1" in line:
            seen["1"] = time.monotonic()
        elif "chunk2" in line:
            seen["2"] = time.monotonic()
        buf = b""
print(f"{seen.get('2', 0) - seen.get('1', 0):.3f}" if len(seen) == 2 else "-1")
PY
)"
  # threshold 1.0s: comfortably below the 1.5s stub gap, above loopback jitter.
  if awk "BEGIN{exit !($gap >= 1.0)}" 2>/dev/null; then
    ok "SSE: chunk 增量到达，间隔 ${gap}s (未缓冲)"
  else
    if [[ "$SMOKE_SSE" == warn ]]; then
      echo "  ⚠ SSE: 间隔 ${gap}s < 1.0s — 疑似被缓冲(warn，不计失败)"
    else
      no "SSE: 间隔 ${gap}s < 1.0s — 响应被缓冲，未实时 flush"
    fi
  fi
}

run_target() {
  echo ""
  echo "═══ 目标: static TLS ═══"
  PASS=0; FAIL=0
  # Fresh maintenance dir (see assert_maintenance for why we never delete the
  # flag once set).
  TMP_MAINT="$MAINT_BASE/caddy"
  FLAG="$TMP_MAINT/MAINTENANCE_ON"
  HOST_HEADER="test.local"
  mkdir -p "$TMP_MAINT"
  cp -R "$ROOT/deploy/maintenance/." "$TMP_MAINT/"
  printf '%s\n' "proxy-contract-note" > "$TMP_MAINT/note.txt"
  rm -f "$FLAG"
  start_proxy
  if ! wait_ready; then
    FAILED_TARGETS+=("caddy(启动失败)")
    docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
    return
  fi
  assert_routing
  assert_swagger_404
  assert_real_ip
  assert_no_technology_headers
  assert_redirect "static redirect" "https://localhost:${HTTPS_PORT}/redirect-check?q=1"
  assert_upload_cap
  assert_sse
  assert_maintenance   # LAST: sets the flag, which we can't reliably clear
  echo "  ── caddy: $PASS 通过 / $FAIL 失败"
  [[ $FAIL -gt 0 ]] && FAILED_TARGETS+=("caddy($FAIL 失败)")
  docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
}

run_acme_target() {
  echo ""
  echo "═══ 目标: ACME entry ═══"
  PASS=0; FAIL=0
  TMP_MAINT="$MAINT_BASE/acme"
  FLAG="$TMP_MAINT/MAINTENANCE_ON"
  HOST_HEADER="localhost"
  mkdir -p "$TMP_MAINT"
  cp -R "$ROOT/deploy/maintenance/." "$TMP_MAINT/"
  start_acme_proxy
  if ! wait_ready; then
    FAILED_TARGETS+=("acme(启动失败)")
    docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
    return
  fi
  assert_no_technology_headers
  assert_redirect "ACME redirect" "https://localhost/redirect-check?q=1"
  echo "  ── ACME entry: $PASS 通过 / $FAIL 失败"
  [[ $FAIL -gt 0 ]] && FAILED_TARGETS+=("acme($FAIL 失败)")
  docker rm -f "$PROXY_CTR" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
preflight
start_stub
run_target
run_acme_target

echo ""
echo "═══════════════════════════════"
if [[ ${#FAILED_TARGETS[@]} -eq 0 ]]; then
  echo "✓ 全部通过 — Caddy 反代契约完好"
  exit 0
else
  echo "✗ 未通过: ${FAILED_TARGETS[*]}"
  exit 1
fi
