# Fleet deploy (fleet.sh)

`fleet.sh` is the single deploy entry point for ArtifactFlow across the whole
range from one box to many. **Single machine is the degenerate case of the
multi-host sequence, not a separate flow** — running it daily keeps the
multi-host path continuously rehearsed, so the two never drift apart.

```
deploy/scripts/fleet.sh preflight            # per-host readiness checks
deploy/scripts/fleet.sh deploy <bundle-dir>  # load → release gate → up → LB → smoke
deploy/scripts/fleet.sh deploy --dry-run <d> # print the plan, touch nothing
deploy/scripts/fleet.sh status               # per-host `compose ps` + LB health
deploy/scripts/fleet.sh rollback             # re-up the previous version
```

## Topology: `deploy/fleet.conf`

Copy `fleet.conf.example` → `fleet.conf` (gitignored — it may carry host IPs).
One row per `(role, host)`:

```
<role>  <host>  [arch=arm64|amd64]  [scale=N]
```

| field | meaning |
|-------|---------|
| role  | `infra` (pg+redis) · `release` (one-shot migrate+reconcile) · `app` (backend×scale + frontend) · `lb` (caddy) |
| host  | ssh-reachable hostname/IP, or literal `local` (run here, no ssh) |
| arch  | validated against the bundle + host `uname -m`; not used to pick tars (see Arch) |
| scale | app rows only — backend replica count; **frontend is always 1** |

Cardinality enforced: exactly one `release`, exactly one `lb`, ≥1 `app`.

**Single box** = every role on `local`:

```
infra    local
release  local
app      local   arch=arm64  scale=2
lb       local
```

## Where fleet.sh runs (control host)

- **1 machine** → run it **on that machine**; `host=local` skips ssh entirely.
  Control host = data host = the one box. No jump host.
- **2+ machines** → run it on an **intranet jump host** that can `ssh` to every
  target. This keeps the air-gap contract intact: the Mac *builds* the bundle,
  the tar is carried in, and a host inside the intranet *drives* the deploy.
  Control host ≠ build host. (You *can* run it on one of the targets instead —
  it treats itself as `local`, others over ssh — but then control and data
  share a box.)

ssh knobs: `AF_SSH_USER` (default current user), `AF_SSH_OPTS`
(e.g. `-i ~/.ssh/fleet -p 2222`), `AF_REMOTE_DIR` (install dir on remotes,
default `/opt/artifactflow`).

## The deploy sequence

`deploy <bundle-dir>` consumes a `scripts/release.sh` bundle (the `dist/`
tars + `artifactflow-*.manifest.txt`). Version and Platform are read from the
manifest — the manifest is the source of truth, nothing is untarred to learn them.

1. **verify** — `verify-bundle.sh` checks every tar's sha256.
2. **arch check** — host `uname -m` must match the bundle Platform (loud-fail;
   informational under `--dry-run`).
3. **load** — `docker load` the app tar (backend + frontend images live in it),
   plus the infra tar if present and an `infra` role is declared.
4. **up** — see single vs multi below.
5. **wait** — poll `/health/ready` through the LB until green (`AF_READY_TIMEOUT`,
   default 120s).
6. **smoke** — one authed-free `/health/ready` hit through the LB.

On success the deployed version is recorded in `deploy/.fleet-state` (gitignored)
as `current`, and the prior `current` becomes `previous` — that's what `rollback`
re-ups. Images for the previous version stay loaded, so rollback is just a
re-`up` at the old `AF_VERSION`; nothing is rebuilt or re-fetched.

### Single-host up

All roles are `local`, so **compose owns the ordering** via its own
`depends_on`: the one-shot `release` service runs under the PG advisory lock and
must exit 0 before backends start (`service_completed_successfully`), then
frontend, then caddy gate on health. fleet.sh just loads images and runs one
`docker compose --profile infra up -d --scale backend=N`.

> Note: single-host `up` recreates changed containers, so there's a brief blip
> during the swap. For a zero-surprise window (maintenance page during the
> swap) use `deploy/scripts/maintenance.sh` / `pause.sh` + `resume.sh` around
> it. True zero-downtime rolling is inherently a multi-host property.

### Multi-host up

Compose `depends_on` is same-host only, so **fleet.sh owns the cross-host
ordering**: infra up → release gate on the release host (`compose run --rm
--no-deps release`, must exit 0) → app hosts brought up **one at a time**,
each waited to `/health/ready` before the next (so a replica is always serving)
→ regenerate the LB's static upstream from the app hosts and `caddy reload` →
smoke through the LB.

## Multi-host: unexercised seams

**Multi-host is authored but has never run against a second machine.** A real
multi-host `deploy` is *gated off* in `fleet.sh` (`cmd_deploy`) — only
`--dry-run` prints the plan. Before removing that guard, validate these three
cross-host seams on the first 2-machine run (they can't be exercised on one box,
and each loud-fails rather than silently misbehaving):

- **(a) backend port publishing.** The base compose only `expose: 8000`
  (compose-internal). A cross-host LB needs backend host-published. Add a small
  fleet overlay compose that publishes the port — **do not edit the single-host
  compose**, or you break the validated docker-DNS `dynamic a` path.
- **(b) static Caddy upstream.** Single-host uses `dynamic a { name backend }`
  (docker DNS resolves the scaled replicas). Across machines there's no shared
  DNS — the LB needs a generated static list (`k1:8000 k2:8000 …`) rendered from
  `fleet.conf`. Active health checks *do* work on static upstreams (they don't
  on `dynamic a`), so this is also when the `health_uri` wedge-removal from the
  single-host tuning starts pulling its weight.
- **(c) per-host `.env` DB/Redis URLs.** App hosts must point `DATABASE_URL` /
  `REDIS_URL` at the **infra host**, not `localhost`. Ship a single-source
  `.env` plus a per-host `.env.<host>` override (also gitignored).

Un-gate by deleting the `die` in `cmd_deploy`'s multi-host branch once (a)–(c)
are in place and a 2-machine `deploy` has run green.

## Arch

`release.sh` builds **one architecture per run** and the tar filenames do **not**
encode it (only the manifest's `Platform:` line does). So `fleet.sh` uses the
`arch` column to **validate** (host `uname -m` vs bundle Platform), not to pick
among multiple tars. `arm64`/`aarch64` and `amd64`/`x86_64` are normalized, so a
`linux/arm64` bundle matches both a Kylin `aarch64` and a Darwin `arm64`.

The current fleet is homogeneous (two Kylin arm64), so one bundle serves all.
**True mixed-arch** (some x86 hosts) would need `release.sh` to emit
arch-suffixed tars (`artifactflow-app-<V>-arm64.tar.gz` / `-amd64`) and
`fleet.sh` to select by the host's `arch` — a change to make only when a mixed
fleet actually exists.

## Infra

The recommended shape is a **dedicated `infra` host running pg+redis via compose**
(`--profile infra`, reusing the `--with-infra` bundle's infra tar); app hosts
don't run a local DB. To use external/managed pg+redis instead, **omit the
`infra` row** and set `DATABASE_URL`/`REDIS_URL` in `.env` — fleet.sh then never
touches infra.
