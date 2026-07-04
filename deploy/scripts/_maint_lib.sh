#!/usr/bin/env bash
# Shared library for the maintenance-window scripts, sourced by both the
# intranet (Mode 3) and public (Mode 2) pause/resume entrypoints — both modes
# front with Caddy (nginx retired).
#
# This file is SOURCED, never executed directly. Before sourcing, the entrypoint
# MUST:
#   - run under `set -euo pipefail`
#   - define: SCRIPT_DIR, COMPOSE_FILE
#   - define: MAINT_MODE_LABEL, MAINT_PROXY_LABEL, MAINT_RESUME_HINT
#   - (optional) MAINT_PROXY_EXTRA — appended after the proxy name in messages
#   - (optional) MAINT_RESUME_ARGHINT — the arg suffix shown after the resume
#     hint. Defaults to " [VERSION]" (intranet, versioned images). Public sets
#     it to "" because its images are built locally as :latest, with no
#     AF_VERSION tag to switch to (see maint_resume).
#   - for resume: a `maint_probe` function — the through-proxy health check,
#     returning 0 on success / non-zero on failure. A default implementation
#     (below: exec into the caddy container, probe its :2021 internal health
#     listener) is provided since both modes now use the same Caddy probe;
#     entrypoints only override it if their proxy topology diverges again
#     (define the override AFTER sourcing this file, or it gets clobbered).
#
# Why the split: the pause/resume MECHANISM is identical across deployments
# (maintenance flag file + stop/start backend+frontend). Only the compose file
# differs. Centralising the logic here stops the intranet and prod scripts
# from drifting apart over time.

# Default through-proxy health probe: exec into the caddy container and hit
# Caddy's internal health listener (`:2021` in caddy-common.caddy — HTTP, no
# TLS, NOT published to the host). The request flows Caddy(:2021) →
# reverse_proxy → backend:8000, so a green result means Caddy is up, its
# config loaded, and the proxy path to backend works. /health is not gated by
# maintenance, so this is safe to run before lifting the flag. Running
# in-container avoids depending on host-published ports and sidesteps the
# TLS-on-localhost hostname mismatch (cert is for the domain, not localhost).
maint_probe() {
  local caddy_cid
  caddy_cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null || true)
  if [[ -z "$caddy_cid" ]]; then
    echo "⚠ caddy 容器未运行，无法验证链路，维护页保持开启"
    echo "  先确保 caddy 已启动：${DC[*]} -f $COMPOSE_FILE up -d caddy"
    return 1
  fi
  # caddy:alpine ships busybox wget (no curl). -q --spider = HEAD-ish probe.
  if ! docker exec "$caddy_cid" wget -q --spider -T 5 http://localhost:2021/health/ready; then
    echo "⚠ /health/ready 经 Caddy(:2021)→backend 不可达，维护页保持开启"
    echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=40 caddy backend"
    return 1
  fi
}

# Pick docker compose CLI: V2 plugin ("docker compose") preferred, V1 standalone
# ("docker-compose") fallback for older intranet CentOS 7 hosts. Both speak the
# compose-file syntax we use. Sets the global DC array.
maint_pick_compose_cli() {
  if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    echo "Error: neither 'docker compose' nor 'docker-compose' available" >&2
    exit 1
  fi
}

# Enter a maintenance window: raise the page, then stop backend / frontend. The
# reverse proxy + Postgres + Redis are left running so the maintenance page stays
# reachable and DB state is preserved. Arg $1 = optional operator note.
maint_pause() {
  local note="${1:-}"
  maint_pick_compose_cli

  if [[ -n "$note" ]]; then
    "$SCRIPT_DIR/maintenance.sh" on "$note"
  else
    "$SCRIPT_DIR/maintenance.sh" on
  fi

  # Brief settle so in-flight requests get the maintenance page rather than a
  # connection drop the instant the upstream goes away.
  sleep 2

  echo "→ Stopping backend / frontend"
  "${DC[@]}" -f "$COMPOSE_FILE" stop backend frontend

  echo
  echo "✓ 维护窗口已开启 (${MAINT_MODE_LABEL})"
  echo "  • 用户访问 → 维护页 (${MAINT_PROXY_LABEL} 仍在运行${MAINT_PROXY_EXTRA:-})"
  echo "  • backend / frontend 已停止"
  echo "  • ${MAINT_PROXY_LABEL} / postgres / redis 仍在运行"
  echo
  echo "下一步：${MAINT_RESUME_HINT}${MAINT_RESUME_ARGHINT- [VERSION]}"
  echo "  仅修改 .env 中 ARTIFACTFLOW_* (backend 用) → 在此编辑 .env 后再 resume。"
}

# Internal: wait for one compose service to report healthy, polling every 2s.
# Args: <service> <label> <timeout_s> <iters>. Returns 0 healthy / 1 timeout.
_maint_wait_healthy() {
  local svc="$1" label="$2" timeout="$3" iters="$4"
  echo -n "→ Waiting for $label healthy (timeout ${timeout}s)"
  local cid state
  for _ in $(seq 1 "$iters"); do
    cid=$("${DC[@]}" -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)
    if [[ -n "$cid" ]]; then
      state=$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
      if [[ "$state" == "healthy" ]]; then
        echo " ✓"
        return 0
      fi
    fi
    printf '.'
    sleep 2
  done
  echo
  echo "✗ $label 未在 ${timeout}s 内 healthy，维护页保持开启"
  echo "  排查：${DC[*]} -f $COMPOSE_FILE logs --tail=80 $svc"
  echo "  慢盘机器可重试：RESUME_HEALTHY_TIMEOUT=120 $0 ${RESUME_VERSION}"
  return 1
}

# Exit a maintenance window: bring backend / frontend up (optionally with a new
# image tag), wait for both healthy, run the mode-specific through-proxy probe,
# then lower the maintenance flag. If anything fails within the timeout the
# maintenance page stays on so a half-broken service is never exposed.
# Arg $1 = version (AF_VERSION), OPTIONAL. Empty/omitted → bring services up at
# whatever the compose file pins (public images are :latest, built locally —
# there is no version tag to switch to). Relies on the entrypoint having defined
# a `maint_probe` function.
maint_resume() {
  local version="${1:-}"
  RESUME_VERSION="$version"   # surfaced in _maint_wait_healthy retry hint
  maint_pick_compose_cli

  # Round up to even seconds for the 2s cadence; refuse anything below 10s (we'd
  # be timing out faster than the healthcheck's own start_period=15s,
  # guaranteeing a false negative).
  local healthy_timeout="${RESUME_HEALTHY_TIMEOUT:-60}"
  if ! [[ "$healthy_timeout" =~ ^[0-9]+$ ]] || (( healthy_timeout < 10 )); then
    echo "Error: RESUME_HEALTHY_TIMEOUT must be an integer ≥ 10 (got: $healthy_timeout)" >&2
    exit 2
  fi
  local healthy_iters=$(( (healthy_timeout + 1) / 2 ))

  if [[ -n "$version" ]]; then
    echo "→ Starting backend / frontend (AF_VERSION=$version)"
    AF_VERSION="$version" "${DC[@]}" -f "$COMPOSE_FILE" up -d backend frontend
  else
    echo "→ Starting backend / frontend"
    "${DC[@]}" -f "$COMPOSE_FILE" up -d backend frontend
  fi

  # Backend AND frontend must both be healthy — the proxy routes `/` to frontend,
  # so a crash-looping frontend would error users the instant maintenance lifts.
  _maint_wait_healthy backend  backend  "$healthy_timeout" "$healthy_iters" || exit 1
  _maint_wait_healthy frontend frontend "$healthy_timeout" "$healthy_iters" || exit 1

  # Mode-specific through-proxy probe (defined by the entrypoint). /health is not
  # gated by maintenance, so this confirms the proxy → backend path is alive
  # before we lift the flag.
  maint_probe || exit 1

  "$SCRIPT_DIR/maintenance.sh" off
  echo
  if [[ -n "$version" ]]; then
    echo "✓ 服务已恢复 (${MAINT_MODE_LABEL})，AF_VERSION=$version"
  else
    echo "✓ 服务已恢复 (${MAINT_MODE_LABEL})"
  fi
}
