# Multi-replica deployment (乙2)

How ArtifactFlow scales to `--scale backend=N` on one host (compose), and the
real-machine checks you MUST run before relying on it.

> Status: the full **Validation** checklist below (minus item 7, reaper) ran
> green on a dev Mac on 2026-07-04 — `--scale backend=2`, Caddy HTTPS entry
> (self-signed cert), real LLM turns, Redis profile. That run also caught and
> fixed three LB bugs (single-upstream pinning → `dynamic a`; single-file
> mount inode pinning → directory mount; wedged-replica removal → passive
> timeouts + caddy healthcheck probe) — see deploy/caddy/common.caddy comments.
> Item 7 (reaper cross-replica reclaim) has NOT been exercised anywhere; run it
> before relying on sandbox turns under multi-replica in production.

## What changed

**Release vs serve split.** Migrate + reconcile no longer run in every backend.
A one-shot `release` service does it once with the authoritative env, under the
PG advisory lock, then exits. Backends gate on its success and only serve:

```
release (entrypoint.sh release)  ──migrate + reconcile, exit 0──┐
                                                                 │ depends_on:
backend ×N (AF_SKIP_RELEASE=1)  ──serve only, no reconcile───────┘ service_completed_successfully
```

- `deploy/entrypoint.sh`: `release` mode (one-shot) + `AF_SKIP_RELEASE` serve
  path. Default (no `AF_SKIP_RELEASE`, no `release` arg) is unchanged — the old
  inline "leader migrates, followers reconcile under the lock" path, kept for
  Mode 1 (SQLite single box) and backward compatibility.
- `docker-compose.prod.yml` and `deploy/docker-compose.intranet.yml` add the
  `release` service + `AF_SKIP_RELEASE=1` on the backend + the gate.
- Reverse proxy is **Caddy** (both modes; intranet nginx retired): it resolves
  `backend:8000` through docker DNS at request time and round-robins across all
  scaled replicas natively — no static-upstream staleness to work around.
  Intranet entry config: `deploy/caddy/Caddyfile.intranet` (static cert HTTPS),
  shared site body in `deploy/caddy/common.caddy`.

## Prerequisites for multi-replica

1. **Redis is mandatory.** The shared RuntimeStore (lease / cancel / interrupt /
   queue / streams) is single-process under the InMemory fallback. Multi-replica
   MUST use Redis — enable the `infra` Redis (`--profile infra`) or point
   `ARTIFACTFLOW_REDIS_URL` at an external one, and set `ARTIFACTFLOW_REDIS_KEY_PREFIX`.
2. Keep `SANDBOX_REAP_ALLOW_LOCAL_STORE=false` (default): the reaper's liveness
   source must be the shared Redis, or replicas would reap each other's sandboxes.

## Enable (intranet, single host)

```bash
docker compose -f deploy/docker-compose.intranet.yml --profile infra up -d --scale backend=2
```

For multi-host, the same `release` step runs once (Ansible: a release task
delegated to one host) before starting backends on all hosts. Not wired here.

## Validation (run on a real box before trusting it)

1. **Single-replica still boots** (regression guard): `up -d` with `backend=1`,
   `docker compose ps` shows `release` exited 0 and `backend` healthy; log in + a
   normal turn works.
2. **Release ran once:** `docker compose logs release` shows migrate + reconcile +
   "Release complete."; backends' logs show "AF_SKIP_RELEASE set — skipping…".
3. **Scale up:** `--scale backend=2` → both backends healthy; exactly ONE release
   ran (not one per backend).
4. **Caddy balances:** hammer a cheap endpoint through the Caddy entry and
   confirm requests land on BOTH backends — the `X-Instance-ID` response header
   names the serving replica, so `curl -sk -o /dev/null -w '%{header_json}'` in a
   loop is enough. Verify single-replica still routes too.
5. **SSE under scale:** start a chat turn through Caddy (HTTPS entry); the
   stream stays on one backend for its life and completes. Reconnect/`/resume`
   still works.
6. **Cross-replica control (Redis):** start a long turn on one backend, cancel it
   from another tab (likely a different backend) — cancel must take effect.
7. **Reaper:** kill a backend mid-sandbox-turn; confirm the lease-anchored reaper on
   another replica reclaims the orphaned sandbox (does NOT reap live ones).

## Rollback

Revert to the inline model: drop the `release` service + `AF_SKIP_RELEASE` from the
backend (it then self-releases on start). The advisory-lock inline path supports
`--scale` too (each replica reconciles under the lock — correct, just redundant).
