# AGENTS.md

## Project Overview

ArtifactFlow is a multi-agent system for **private-deployment AI services** (see README §项目定位). Its Pi-style execution engine is configured through MD/YAML agents, tools, and models rather than Python extension classes. A `lead_agent` delegates context-isolated work to subagents; Task Plan and Result artifacts persist execution state across turns. **Python 3.11+.**

The public Wiki covers product behavior, configuration, and operations. This file records engineering constraints and non-local invariants that cannot be recovered safely from one source file.

Source map: `src/core/execution/` is the runtime kernel; `src/core/management/` owns application use cases; `src/core/capabilities/` resolves effective skills/tools; `src/core/security/` contains shared security logic. HTTP adapters live in `src/api/routers/`, with admin-only adapters in `src/api/routers/admin/`. The frontend groups independently loaded/administered functionality under `frontend/src/features/`; cross-feature presentation primitives belong in `frontend/src/components/ui/`.

## Application Boundaries

Application boundaries follow dependency direction and orchestration ownership.

- **Backend HTTP:** `Router → Manager → Repository → DB`. Routers own auth, parsing, and HTTP mapping; Managers own use-case decisions and call ordering; Repositories own persistence. Routers must not import Repositories. Repositories return ORM objects without formatting or business logic, and ORM objects must not escape their loading session.
- **Other backend entry paths:** execution, tools, and background jobs need not imitate the HTTP CRUD layers, but each workflow must have one orchestration owner and must not depend on API adapters.
- **Frontend:** `App shell → Feature → Shared`. The shell composes screens and modes; a feature owns its components, hooks, state coordination, and API bindings; shared UI/API/utilities must not import feature code. Do not let unrelated features accumulate in generic directories such as `chat`, `forms`, or `lib`; when touching a hotspot, move code toward its owning feature when that clarifies the boundary.
- **Orchestration threshold:** a single local operation may remain direct and simple. When correctness depends on multiple calls, navigation freshness, SSE lifecycle, or coordinated state writes, one use-case owner controls the sequence: a Manager on the backend or a feature-level coordinator on the frontend. Transport clients only transport; components render state or trigger actions.

## Development Workflow

### Essential Commands

```bash
# Both secrets are mandatory; the server will not start without them.
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
echo "ARTIFACTFLOW_CREDENTIAL_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> .env

# Run before frontend work that depends on API schemas.
python scripts/export_openapi.py && (cd frontend && npm run generate-types)

# Debug logging.
ARTIFACTFLOW_DEBUG=true
```

### Dependency Lockfile

`requirements.txt` is the abstract `>=` source of truth; `requirements.lock` is the pinned set installed by the Docker image. Any dependency change in `requirements.txt` must regenerate the lock inside `python:3.11-slim`, then run a CVE audit:

```bash
docker run --rm -v "$PWD":/w -w /w python:3.11-slim sh -c \
  "pip install -q pip-tools && pip-compile --quiet --no-emit-index-url \
   --output-file=requirements.lock requirements.txt"
docker run --rm -v "$PWD":/w -w /w python:3.11-slim sh -c \
  "pip install -q pip-audit && pip-audit -r requirements.lock"
```

Use `--upgrade` or `--upgrade-package X` only when intentionally refreshing pins.

### Git and Branching

- Run `git branch --show-current` before staging or committing. Generic changes land on `main`; `intranet` is only for deployment-specific changes that do not generalize.
- Never use `git add -A` or `git add -u`; parallel sessions may share this worktree. Stage explicit files and verify the commit with `git show --stat`.
- Before an intranet release, require an `intranet` checkout and an empty `git log intranet..main`, or the package may omit intranet-only overrides.

## Cross-Cutting Safety and Correctness

- **Security uses 404, not 403, for cross-user resources.** This avoids leaking existence. Authentication stays at the API boundary; execution core and tools receive `user_id` as plain data.
- **Admin scope is user management, not data management.** Admin APIs may observe user-owned resources but must not edit or delete another user's conversations, artifacts, or messages without an explicit product decision. User deletion and FK cascade are the normal cleanup path.
- **Every error exit serves users and operations.** Return a sanitized message and decide separately whether to log, joined by `request_id`. Always log 5xx. Log 4xx only when the reason is non-obvious or likely to be reported; skip routine 401/403/404/422. Use `warning` for expected/handled failures, `error` for server failures without a useful stack, and `exception` inside `except` when the caught error may be a bug.
- **Errors preserve diagnostics without leaking values.** `MessageEvent` stores raw errors; user-facing SSE/replay sanitize unless DEBUG, while admin observability remains raw. Freeze `request_id` and `instance_id` into error events at creation, and keep instance/request/conversation/message locators in logs. Every runtime `DatabaseManager` engine uses `hide_parameters=True`; retain SQL/error structure and add only allowlisted business identifiers to logs, never bound values.
- **Persistence time is naive UTC.** Use `utils.time.utc_now()` instead of local `datetime.now()` and keep persisted columns as naive `DateTime`. Retain PostgreSQL UTC enforcement in both deployment configuration and connection setup (`src/db/database.py::_apply_session_tz_kwargs`), because either may be the only effective layer in a deployment. Exceptions are prompt-local display time and aware-UTC JWT claims.
- **Resource limits fail loud.** Prefer rejection, timeout, `noeviction`, or explicit error over silent eviction, drop-oldest, or implicit sharing. TTL'd Redis keys and similar control state are not disposable caches unless explicitly proven otherwise.

## Execution and Persistence Decisions

### Agent Execution and History

- **Execution is Pi-style and nested-serial** (`src/core/execution/engine.py`). `call_subagent` recurses in place, so a round may naturally mix tools and subagents while preserving one asyncio task, one active agent, and event order equal to execution order. Same-round tool parameters remain frozen at generation; later calls may still observe artifacts written by earlier serial calls.
- **Agent completion is return-value routing.** A no-tool reply returns from `_run_agent`: top-level becomes `COMPLETE`/response, while a subagent reply becomes the caller's `call_subagent` result and remaining same-round calls continue. `None` means a terminal path already set `stop_reason`, so callers unwind and skip remaining work. Native-call closure completes every accepted call exactly once before persistence.
- **Agents are data, and the lead prompt is registry-agnostic.** Agent definitions live in `config/agents/*.md`. `lead_agent.md` contains generic delegation criteria; subagent-specific routing belongs in each subagent `description`, which runtime exposes through `<available_subagents>`.
- **MessageEvent is the execution history.** Events are append-only; `llm_chunk` is SSE-only and `llm_complete` carries persisted content. LLM history is reconstructed from `MessageEvent`, not display-only `Message.user_input`/`response`. Historical events load with `is_historical=True`; only current-turn events are batch-written. `EventHistory` filters per agent and scans back to its latest compaction or fresh-start boundary.
- **Failed turns still support continuation.** Persist events even when a turn fails; the next turn skips failed markers and continues from recorded history. Do not copy partial `response_content` into the display response on an error—the complete model content already lives in `llm_complete`.
- **Permission interrupts outlive the stream.** `CONFIRM` tools await the RuntimeStore interrupt mechanism. Only timeout, store shutdown, or Pub/Sub failure deny by resolving `None`; an SSE disconnect does not deny, and the user may reconnect and `POST /resume`.

### Terminal Handling and Compaction

- **One dispatcher owns every terminal path** (`src/core/execution/agent_runtime.py`, `src/api/services/conversation_turn_handler.py`, `src/core/execution/post_processing.py`). `AgentRuntime` reports factual state and stop reason but never emits the final terminal. `ConversationTurnHandler` owns artifact flush, native-call closure, terminal choice, event persistence, and display writes. After runtime returns, `PostProcessState.stop_reason` remains authoritative; artifact flush failure has highest precedence.
- Preserve three ordering invariants: persist events before `Message.response`; set `response_update_attempted` before awaiting the response write; and ignore `is_historical=True` when finding the current turn's terminal.
- **Timeout is an execution result, not transport authority.** The deadline wraps only `execute_loop`; the shared dispatcher emits `TIMED_OUT`, while post-processing stays outside the execution timeout. Disable PostgreSQL command timeout with `ARTIFACTFLOW_DB_COMMAND_TIMEOUT=0`, never a non-positive asyncpg URL value.
- **The engine records errors but does not emit their terminal.** It sets `StopReason.ERROR`, raw `error_detail`, and a safe display response; post-processing creates the single ERROR terminal. Persistence and post-processing failures are the explicit transport-level exceptions.
- **Compaction creates a per-agent history boundary** (`src/core/execution/compaction_runner.py`). The summary is the agent's sole memory of compacted events, so it must preserve in-flight work. Compaction failure and a second typed overflow fail loud without a placeholder boundary. `carry_tool_call` carries only an outstanding request half, never an arbitrary recent tail. Tool-result size remains the tool author's responsibility; generic compaction must not silently truncate it. Keep `compact_agent.context_window` at least as large as every runtime agent window and retain validation both before reconciliation and at startup.

### Persistence and Concurrency

- **Repositories own transaction completion.** `DatabaseManager.session()` creates and closes sessions; Repository writes flush and commit to keep locks short. Cross-Repository atomicity is intentionally sacrificed: artifact, event, and response writes may commit independently. Do not change this without an explicit transaction-ownership redesign.
- **Required persistence fails closed at assembly.** Durable workflows must never degrade into success, empty reads, or no-ops when a Repository or DB manager is absent. `ConversationTurnHandler` accepts either production short sessions or a complete bound manager/repository pair and rejects incomplete or mixed wiring. Explicit optional capabilities such as SSE emission and documented in-memory RuntimeStore mode remain allowed.
- **Conversation send and delete share one admission lease** (`src/api/services/conversation_execution_service.py`, `conversation_lease.py`). Acquire before the authoritative DB create/require/delete; acquisition is the linearization point, and the admitted task inherits the same handle without reacquiring. Ownership uncertainty fails closed; release and stale cleanup remain owner-CAS. Existing IDs use `require_owned`/FK constraints so concurrent deletion cannot resurrect rows.
- **ORM instances are short-lived snapshots, not runtime state.** Expired async-session attribute access may trigger implicit IO and `MissingGreenlet`. Use `server_default=func.now()` for creation and `onupdate=func.now()` for updates. Prefer ORM mutation for already-dirty rows; use bulk `UPDATE` only for DB-side values without another change, never assign SQL expressions to instance attributes. After bulk update/commit, refresh or re-query affected same-session instances.
- **All Redis access is standalone, Sentinel, and Cluster safe.** Multi-key operations may touch only keys sharing one entity hash slot. Cross-entity aggregation fans out with `pipeline(transaction=False)`; do not use cluster-only APIs such as `mget_nonatomic`. State the slot of every new multi-key operation in review.

## Tool, Sandbox, and Artifact Decisions

- **A tool is the integration unit.** One tool represents one permissioned operation. Trusted backend tools own network I/O; model-driven CLI work stays in the sandbox. Multi-endpoint APIs become one tool MD per operation, with multi-step orchestration in skills. MCP is a future provider path, not a replacement for tool semantics.
- **Tool schemas are one contract.** Each tool owns a root-object JSON Schema Draft 2020-12 `input_schema`; export it unchanged and validate runtime arguments against the same schema. OpenAI-compatible streamed function names and arguments are append-only deltas, never cumulative snapshots. Tools return `ToolResult`; streaming uses `core.execution.events.StreamEventType`.
- **Model-facing XML-like text is not a machine protocol.** Native function calls and `tool_call_id` are the parsed outer protocol. Escape untrusted labels such as artifact titles, but keep artifact bodies raw because update matching depends on exact content.
- **Minimize model-facing parameters.** Expose only choices with semantic intent. Hidden caps and reserves belong in `src/config.py`; surface their consequences through tool hints rather than making them tunable. Permission patterns have different security weight: sandboxed bash allowlists reduce UX friction, while egress allowlists are exact, default-deny exfiltration boundaries.
- **Sandbox egress stays staged.** Containers keep `--network=none`; trusted backend tools fetch intranet/API data and materialize local inputs. Any future egress allowlist requires a TLS-terminating policy proxy, not a simple IP/domain opening.
- **Sandbox recovery is empty, one-shot, and never replayed** (`src/tools/builtin/sandbox_session.py`). A runtime failure remains failed; one recovery may delete the old container and entire scratch tree before starting generation 2. Pre-recovery model invocations are fenced by `model_invocation_epoch`; uncertain cleanup, a second failure, or replacement failure stays sticky for the turn.
- **Tool authors bound CPU and output cost.** Async cancellation is cooperative, so synchronous or GIL-bound work needs an algorithmic bound plus a wall-clock guard. Tools also own pagination/truncation with an explicit consequence; the engine cannot safely infer how to trim semantic output.
- **Artifact writes use four layers** (`src/tools/builtin/artifact_service.py`, `artifact_working_set.py`). `ArtifactService` orchestrates events over an exclusive pure-state WorkingSet, Repository, and pure algorithms. Turn-local edits emit SSE-only `ARTIFACT_*` events and flush once, so persisted version numbers may be sparse. REST reads remain DB-only; live overlays are not cross-worker safe. Uploads stage through the same write-back path and roll back on staging failure.
- **Quotas and retention are separate.** Guard user-driven blob growth at `ArtifactService.create_from_upload`. Do not silently TTL-delete passive message/event growth; prefer explicit conversation archival if retention becomes necessary.

## Frontend Decisions

- **Authenticated SSE uses `fetch` plus `ReadableStream`, not `EventSource`,** because the client must send an `Authorization` header.
- **Frontend locks are UX; backend locks are correctness.** If bypass can corrupt durable/shared state or hurt another actor, enforce the gate server-side and mirror it in the UI. If bypass only produces a stale result for that caller, keep the backend permissive and reconcile client-side.
- **Reconciliation cannot exceed its evidence.** A complete authoritative snapshot may replace/prune; a partial or stale snapshot may only merge; an entity-level not-found removes only that entity. Freshness tokens decide whether a response may commit, not whether it has wider authority. Encode these as distinct transitions rather than a generic setter.

## Deployment Decisions

- **afctl has one state and one apply.** `control/site.toml` is the deployment-capability contract; target-local mutable data stays under `control/`. Immutable effective releases live in `.artifactflow/releases/<release-id>`, and `.artifactflow/state.json` is the only active/previous authority. Keep `plan` read-only; all mutations share one kernel lock and write state only after Caddy health. Do not introduce a second state/symlink, writable release paths, mutable image refs, implicit host provisioning, or Go-based multi-host SSH. Production sandboxing uses runsc; reduced-isolation runc is explicit. Multi-host ordering remains in the pinned Ansible EE.
- **App-only releases contain no infrastructure at the build boundary.** `release.sh --app-only` does not resolve or declare Caddy/PostgreSQL/Redis images. afctl inherits those refs from the current effective target release; without one, fail loud and require `--with-infra`. Ignore legacy app-only declared infra refs in favor of current target state.

## Testing and Documentation

- **Pytest is parallel-safe by default.** Use `tmp_path`, unique entity IDs/Redis namespaces, and dynamic ports; never rely on collection order or shared mutable host state. `external` means a provisioned service, not serial execution. Use `serial` only for a demonstrated, unisolatable conflict and add the test to the serial CI lane. The routine lane is `pytest -n 4 -m "not external and not serial"`.
- **Manual test filenames must not start with `test_`.** Scripts in `tests/manual/` require external services and must not be auto-collected.
- **Active docs are self-contained.** In `docs/` outside `docs/_archive/`, include durable rationale directly rather than pointing to PRs, fix plans, or archived documents as the only explanation.
