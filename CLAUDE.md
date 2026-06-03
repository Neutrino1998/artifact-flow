# CLAUDE.md

## Project Overview

ArtifactFlow is a multi-agent system for **private-deployment AI services** (see README §项目定位). Pi-style execution engine, fully configured via MD/YAML (agents/tools/models — no Python for extensions). A `lead_agent` coordinator delegates to subagents (e.g. `research_agent`) for context-isolated exploration; Task Plan + Result artifacts persist execution state across turns. **Python 3.11+.**

Architecture deep-dives live in `docs/architecture/`. This file holds only the non-obvious *why* you can't recover by reading the code — each entry points to the doc/file where the mechanics live.

## Essential Commands

```bash
# JWT secret — server won't start without it
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

# API type sync — MUST run before frontend code that depends on API schemas
python scripts/export_openapi.py && (cd frontend && npm run generate-types)

# Debug logging: ARTIFACTFLOW_DEBUG=true
```

**Dependency lockfile (DEP-02):** `requirements.txt` is the abstract `>=` source of truth; `requirements.lock` is the pinned set the Docker image installs. **Any time you add/remove/bump in `requirements.txt`, regenerate the lock** — otherwise the image silently keeps the old pins and your new dependency is absent from the build. Regenerate *inside* `python:3.11-slim` so the pins match the deploy interpreter + linux markers, then CVE-audit before committing (lock format is plain `pkg==ver`, no tool lock-in):

```bash
docker run --rm -v "$PWD":/w -w /w python:3.11-slim sh -c \
  "pip install -q pip-tools && pip-compile --quiet --no-emit-index-url \
   --output-file=requirements.lock requirements.txt"   # --upgrade[-package X] to bump
docker run --rm -v "$PWD":/w -w /w python:3.11-slim sh -c \
  "pip install -q pip-audit && pip-audit -r requirements.lock"
```

## Architecture Decisions

Non-obvious design choices you won't infer from reading one file.

- **Pi-style engine** (`src/core/engine.py`): Flat `while not completed` loop — no middleware chain, no framework. Agent calls, tool execution, routing all happen in one loop. Reference: [Pi agent](https://github.com/badlogic/pi-mono).

- **Agent completion routing**: Lead agent with no tool calls → `completed = True` (exit loop). Subagent with no tool calls → pack response as a `call_subagent` tool_result and switch back to lead. This asymmetry is intentional.

- **Agents are data, not classes**: Each agent is an MD file (`config/agents/*.md`) — YAML frontmatter (model, max_tool_rounds, tool permissions) + role-prompt body. No Python to define an agent.

- **Transaction ownership**: `DatabaseManager.session()` manages lifecycle only (create + close); transaction control (`flush` + `commit`) lives in Repository methods to keep write locks short. **Consequence: cross-Repository atomicity is intentionally sacrificed.** Post-processing writes in independent transactions (artifact flush → event persist → `Message.response`/metadata); a later-step failure can leave earlier-committed artifacts with no supporting event history. Accepted trade-off — a single transaction would mean splitting `write`/`commit` across every Repo method (philosophy change, deferred until a user-visible issue).

- **Event sourcing**: All execution events are append-only to the `MessageEvent` table. `llm_chunk` is SSE-only (streaming transport), NOT persisted — `llm_complete` carries the full content.

- **SSE transport**: Frontend uses `fetch` + `ReadableStream`, not `EventSource`, because EventSource cannot send a custom `Authorization` header.

- **Security: 404 not 403**: Cross-user access returns 404 to avoid leaking resource existence. Auth stays at the API boundary only — core/engine/tools receive `user_id` as a plain field.

- **Error sanitization & `request_id` are separate read-boundary concerns** (mechanics: `docs/architecture/observability.md`): `MessageEvent` stores the **raw** error text (event sourcing = full audit; DEBUG replay must show devs the real error). Sanitization (`error` → `"Internal server error"`) is DEBUG-gated at **both** user-facing read boundaries — live SSE push **and** replay — so reload behaves like live; **admin observability endpoints are deliberately NOT sanitized**. The locator `request_id` is **frozen into the error event's `data` at creation** (inherits the originating POST's id via the `asyncio.create_task` contextvar copy), NOT injected at the read boundary where `get_request_id()` would return the *replay GET's* id — the wrong locator. File logs carry all three `[request_id|conv_id|message_id]`; `message_id`/`conv_id` are the bridge to the admin monitor + `MessageEvent` and are never dropped.

- **Permission interrupts**: `CONFIRM`-level tools trigger `RuntimeStore.create_interrupt()`, which blocks on `asyncio.Event`. Timeout and client disconnect are both treated as deny. Multi-tool turns execute serially, so interrupts naturally slot between tools.

- **Tool authors own CPU-cost discipline**: The cancel / timeout / lease-fencing stack is built on `asyncio.Task.cancel()`, which is **cooperative** — a synchronous CPU-bound tool (no `await`, or one pinning the GIL via a C extension) punches through all of them at once (`wait_for` is itself `task.cancel()`; `to_thread` doesn't help once a C extension holds the GIL). Tools must bound CPU cost themselves — algorithmic upper bound + a wall-clock deadline as a second guard (mirror `update_artifact`'s `MAX_UNIQUE_CENTERS` + `MAX_FUZZY_WALL_CLOCK_MS`). The engine cannot semantically bound something only the tool author understands. The 2026-05-14 incident (`docs/_archive/ops/incident-2026-05-14-eventloop-wedge.md`) wedged the event loop for 96 min on exactly this.

- **All cancel/terminal paths funnel through one dispatcher** (three cancel paths in full: `docs/architecture/execution-lifecycle.md`): Every path — cooperative cancel, external cancel during the loop, late cancel during post-processing — **persists events unconditionally** and writes `Message.response` (the frontend hides any turn whose response is empty). The response text comes from a single `core/post_processing.choose_response_for_terminal(pp)` shared by all paths, so they can't drift. Two invariants that are easy to violate: (a) **events-first** — `Message.response` is written only after `_persist_events` succeeds; (b) **slot-claim before await** — `pp.response_update_attempted` is set *before* the `update_response` await, to survive cancel-mid-await (the DB may commit before Python reaches a post-await flag). Also: `ensure_terminal` MUST filter `is_historical=True` — otherwise a multi-turn late-cancel adopts the parent turn's terminal and the current turn lands in the DB terminal-less.

- **Execution timeout is a first-class terminal, not a transport-layer authority** (`docs/architecture/execution-lifecycle.md`): `TIMED_OUT` is produced by the *same* `decide_terminal` dispatcher as every other terminal (precedence `flush_error > {timed_out, cancelled} > error > complete`). The deadline wraps only `execute_loop` inside `run_engine`; `run_and_push` is a pure forwarder. There must be **no second terminal authority** at the transport layer — the old "SSE says timed-out while DB recorded CANCELLED/COMPLETE" split is the bug this design fixes. Post-processing is deliberately *outside* the timeout; per-query bounds are the DB layer's job (PG `command_timeout` via `config.DB_COMMAND_TIMEOUT` — asyncpg rejects ≤0, so disable with `ARTIFACTFLOW_DB_COMMAND_TIMEOUT=0`, never `?command_timeout=0`).

- **Engine error path records, it doesn't emit**: On an unrecoverable error the engine sets `state["error"]=True` + records `state["error_detail"]` (error/agent/request_id) and breaks — it does **not** emit an ERROR event. The single ERROR terminal is built post-flush by `decide_terminal`, so engine-errors get a correct `artifacts_flushed` and there's no double-emit. The two transport-layer ERRORs (events-persist failure, post-processing exception) bypass `decide_terminal` but still carry `artifacts_flushed` via `uploads_persisted(pp)`.

- **Three-layer responsibility model**:
  - **Repository** (`src/repositories/`): pure data access — returns ORM objects, no formatting/business logic. ORM objects must not escape their loading session.
  - **Manager** (`src/core/conversation_manager.py`, `src/tools/builtin/artifact_service.py`): use-case orchestration — ownership checks, formatting, write-back, serialization. Routers must not bypass Manager to hit Repo directly.
  - **Router** (`src/api/routers/`): transport only — auth, parsing, HTTP mapping. No business logic, no Repo imports.

- **Artifact write-back, four layers** (full: `docs/architecture/artifacts.md`): `ArtifactService` orchestrates + emits events and owns an exclusive `ArtifactWorkingSet` (pure state), over `ArtifactRepository` + pure-algorithm modules (old `ArtifactManager` god-object deleted). During a turn, create/update/rewrite only touch the WorkingSet + emit **SSE-only** `ARTIFACT_CREATED`/`ARTIFACT_UPDATED` events; `flush_all()` persists once in post-processing → `ArtifactVersion` numbers are **sparse** (intra-turn edits fold into one record). Non-obvious: (1) **REST reads are pure DB** — the old `_active_managers` overlay was deleted because it silently failed across workers (a REST hit on a non-executing worker can't see the executing worker's memory); turn-live content reaches the frontend *only* via the `ARTIFACT_*` events (not persisted — artifacts have their own durable home). (2) **Uploads are unified into write-back** (staged in-engine via `create_from_upload`, not immediate-commit); staging failure rolls back and the terminal's `artifacts_flushed` is corrected to false via `uploads_rolled_back`.

- **History is MessageEvent, not Message fields** (full: `docs/architecture/engine.md`): LLM history is reconstructed from `MessageEvent` along the conversation path — `Message.user_input`/`response` are display-only. Controller loads path events as `is_historical=True`; the engine appends `is_historical=False` (only these are batch-written at turn end). `EventHistory` filters by `agent_name`, then scans right-to-left to the most recent boundary (`compaction_summary`, or `subagent_instruction.fresh_start=True` for subagents).

- **End turn ≠ block next-turn continuation** (`docs/architecture/engine.md`): Unrecoverable errors end the turn loudly, but events persist unconditionally — the next message's history includes the failed turn (`EventHistory` skips `success=False` markers) so the LLM continues from where it left off, unless the user retries/branches. Corollary: don't pack partial `response_content` into `state["response"]` on error paths — `Message.response` is a display-only snapshot decoupled from history; the full content is already in `llm_complete`.

- **In-engine compaction** (full: `docs/architecture/engine.md`): Runs in-loop after each LLM call once tokens exceed `COMPACTION_TOKEN_THRESHOLD`; the per-`agent_name` `compaction_summary` becomes `EventHistory`'s right-to-left boundary, so everything before it is invisible to that agent. Non-obvious rules:
  1. The summary is the agent's **sole** memory of compacted events — `compact_agent`'s *Current Work* / *Next Step* must carry in-flight state (e.g. "called tool X, result pending") or tool results appended after the summary become uninterpretable.
  2. Compaction does **NOT** backstop tool-result overflow — a huge tool blob *after* compaction can still overflow the next call. By design: generic truncation can't semantically summarize structured output. **Tool authors own output-size discipline** (max_length / pagination / explicit `(truncated at N)`); overflow fails loudly, never silently.
  3. Loud-fail on compaction LLM failure — no placeholder boundary (that would erase the just-emitted `llm_complete` and orphan the next `tool_result`).

## Conventions

- **Tool I/O contract**: tool calls are XML with CDATA-wrapped params (`<![CDATA[...]]>`, parsed via `xml.etree.ElementTree`); tools return `ToolResult`, agents return `AgentResponse`. All streaming uses the unified `StreamEventType` from `core/events.py`.

- **Minimize tool parameter surface.** Expose only params the model has semantic intent to control (`pattern`, `id`, `offset`, `limit`, …). Implementation knobs — caps, thresholds, internal limits — go in `src/config.py` as hidden constants (`READ_ARTIFACT_MAX_CHARS`, `COMPACTION_TOKEN_THRESHOLD`, …), never as tool params. When a hidden cap is hit, surface the *consequence* via `hint`/`summary` so the model can react, but don't let it tune the cap. Rationale: every param is cognitive overhead for small models; operator-tunable limits don't need to be model-tunable.

- **`tests/manual/` file names must NOT start with `test_`** — those scripts need external services (API keys, running server, Redis) and would otherwise be auto-collected by pytest (e.g. `coalescer_bench.py`, `api_smoke.py`).

- **Frontend locks are UX; backend locks are correctness — discriminator: who gets hurt if the gate is bypassed.** Bypass corrupts shared/durable state or hurts another actor (writes/execution) → enforce server-side (lease `409` / ownership `404`); the frontend control is just a mirror. Bypass only gives the caller a stale/odd result for themselves (reads) → frontend-only gate, backend stays permissive, client reconciles.

- **ORM instances are short-lived persistence snapshots, not runtime state containers.** Async-session attribute access on an expired instance triggers implicit IO → `MissingGreenlet`. Rules:
  - Timestamps: `server_default=func.now()` for creation, `onupdate=func.now()` for updates — never assign `datetime.now()` in repo code.
  - Prefer ORM attribute mutation when the row is already dirty (lets `onupdate` fire). Use bulk `UPDATE` only when the row needs a DB-side value (e.g. `func.now()`) with no other change — never assign a SQL expression to an instance attribute.
  - After bulk UPDATE / commit, treat same-session instances of affected rows as stale — `refresh()` or re-query.

- **Redis Cluster-safety: standalone/Sentinel is baseline, but every access must also be Cluster-safe.** Prod Redis form is mixed (standalone / Sentinel / Cluster) and `dependencies.py` picks the client by `REDIS_CLUSTER`, so the *same code* must be correct on all forms. (1) **No multi-key command may span entities** — keys are hash-tagged `{prefix:id}` so one entity's keys share a slot, but distinct entities scatter → `MGET`/`DEL`/`SINTER` across them raise `CROSSSLOT`. (2) **Cross-entity aggregation fans out** via a non-transactional pipeline of per-key ops (`pipeline(transaction=False)`) — never a cluster-only API like `mget_nonatomic` (it `AttributeError`s on standalone `Redis`). (3) State the slot of any new multi-key op in review. Reference fan-out: `list_active_executions` (scan + pipelined GET). This axis is key-routing only — Pub/Sub broadcast and failover are separate concerns.

- **Every error exit serves two audiences — sanitize (user) + log (ops), joined by `request_id`** (project specifics of the global logging rule): the recurring silent-failure is an error branch that returns a sanitized message but skips the ops log. At each non-2xx exit decide **(1) log or not** — 5xx always; 4xx only when non-obvious / user-likely-to-report (upload format/size, business-rule rejects), skip self-evident ones (404, 401/403, 422). Discriminator: *"with just `request_id` + access log, can ops answer why?"* **(2) level** — `warning` = expected/client-caused/handled; `error` = server-side failure with no useful stack; `exception` = inside an `except` where the caught error may be a bug.

- **Time: all Python datetime is naive UTC via `utils.time.utc_now()`** — never `datetime.now()` (local). Columns stay `DateTime` (no `timezone=True`); Python writes and DB-side `func.now()` must both produce naive UTC. PG session tz is forced to UTC at **two layers** — compose `-c timezone=UTC` flags **and** connection-level `connect_args` (`db/database.py._apply_session_tz_kwargs`) — the connection layer is mandatory because cloud-managed PG / failover nodes have a server tz we don't control and compose flags don't reach them. Intentional exceptions: `context_manager` keeps local time for the "current time" injected into prompts (user-local is the UX); `auth` uses aware UTC for JWT `iat`/`exp` (PyJWT contract). Background: incident 2026-05-14 — Shanghai obs queries were off by 8h when Python wrote local-naive but compared as UTC-naive.
