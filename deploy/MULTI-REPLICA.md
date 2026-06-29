# Multi-replica deployment (乙2)

How ArtifactFlow scales to `--scale backend=N` on one host (compose), and the
real-machine checks you MUST run before relying on it.

> Status: the code/config below is implemented and statically validated
> (`docker compose config`, `sh -n`), but the runtime behavior under `--scale`
> has NOT been exercised on a live box. Run the **Validation** checklist before
> trusting it in production.

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
- `deploy/nginx.conf`: backend is proxied via a **variable** (`$af_backend`) so
  nginx re-resolves through docker DNS and round-robins across scaled replicas.
  A static `upstream { server backend:8000; }` resolves once at boot and pins to
  one replica. Trade-off: variable proxy_pass loses upstream keepalive (fine on
  the internal docker network). Prod uses Caddy, which re-resolves automatically.

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
4. **nginx balances:** hammer `GET /api/v1/meta` (or any cheap authed endpoint) and
   confirm requests land on BOTH backends (compare container logs / a per-instance
   marker). This is the part most likely to surprise — verify the variable
   proxy_pass actually round-robins and that single-replica still routes.
5. **SSE under scale:** start a chat turn through nginx; the stream stays on one
   backend for its life and completes. Reconnect/`/resume` still works.
6. **Cross-replica control (Redis):** start a long turn on one backend, cancel it
   from another tab (likely a different backend) — cancel must take effect.
7. **Reaper:** kill a backend mid-sandbox-turn; confirm the lease-anchored reaper on
   another replica reclaims the orphaned sandbox (does NOT reap live ones).

## Rollback

Revert to the inline model: drop the `release` service + `AF_SKIP_RELEASE` from the
backend (it then self-releases on start), and restore the static `upstream backend`
in `deploy/nginx.conf`. The advisory-lock inline path supports `--scale` too (each
replica reconciles under the lock — correct, just redundant).
