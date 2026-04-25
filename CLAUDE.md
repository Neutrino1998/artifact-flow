# CLAUDE.md

## Project Overview

ArtifactFlow is a multi-agent system with a [Pi-style](https://github.com/badlogic/pi-mono) execution engine. It uses a dual-artifact architecture (Task Plan Artifact + Result Artifact) with specialized AI agents (Lead, Search, Crawl) that collaborate to perform tasks.

**Requirements:** Python 3.11+

## Essential Commands

```bash
# JWT secret (required, server won't start without it)
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

# API type sync (MUST run before writing frontend code that depends on API schemas)
python scripts/export_openapi.py                # Export OpenAPI JSON
cd frontend && npm run generate-types           # Regenerate TS types

# Debug logging
ARTIFACTFLOW_DEBUG=true
```

## Architecture Decisions

These are non-obvious design choices that you won't easily infer from reading the code:

- **Pi-style engine** (`src/core/engine.py`): Flat `while not completed` loop — no middleware chain, no framework. Agent calls, tool execution, and routing all happen in one loop. Reference: [Pi agent](https://github.com/badlogic/pi-mono).

- **Agent completion routing**: Lead agent with no tool calls → `completed = True` (exit loop). Subagent with no tool calls → pack response as `call_subagent` tool_result, switch back to lead. This asymmetry is intentional.

- **Agents are data, not classes**: Each agent is an MD file (`config/agents/*.md`) with YAML frontmatter (model, max_tool_rounds, tool permissions) and a role prompt body. No Python code needed to define agents.

- **Transaction ownership**: `DatabaseManager.session()` only manages lifecycle (create + close). Transaction control (`flush` + `commit`) is in Repository methods to keep write locks short. **Consequence: cross-Repository atomicity is intentionally sacrificed.** `post_process` writes in three independent transactions — artifact flush → event persist → `Message.response` / metadata update — each with its own retry. If events persist fails, controller marks the terminal as ERROR and skips `Message.response` / metadata update, but any artifacts already committed in the prior step stay. The next turn's artifact inventory can therefore show artifacts that have no supporting event history. Accepted trade-off; composing a single transaction would require splitting `write` / `commit` across every Repo method (philosophy change, deferred until observed user-visible issues).

- **Event sourcing**: All execution events are append-only persisted to `MessageEvent` table. `llm_chunk` is SSE-only (streaming transport), NOT persisted — `llm_complete` has the full content.

- **SSE transport**: Frontend uses `fetch` + `ReadableStream` (not `EventSource`) because EventSource cannot send custom `Authorization` headers.

- **Security: 404 not 403**: Cross-user access returns 404 to avoid leaking resource existence. Auth stays at API boundary only — core/engine/tools receive `user_id` as a plain field.

- **Permission interrupts**: `CONFIRM`-level tools trigger `RuntimeStore.create_interrupt()` which blocks on `asyncio.Event`. Timeout and client disconnect both treated as deny. Multi-tool turns execute serially so interrupts naturally slot between tools.

- **Three-layer responsibility model**:
  - **Repository** (`src/repositories/`): Pure data access — returns ORM objects, no formatting/serialization/business logic. ORM objects must not escape the session that loaded them.
  - **Manager** (`src/core/conversation_manager.py`, `src/tools/builtin/artifact_ops.py`): Use-case orchestration — ownership checks, history formatting, artifact write-back, serialization to dicts. Routers must not bypass Manager to call Repo directly.
  - **Router** (`src/api/routers/`): Transport layer — auth, parameter parsing, HTTP mapping. No business logic, no Repo imports.

- **Artifact write-back** (`ArtifactManager`): During engine execution, `create_artifact`/`update_artifact`/`rewrite_artifact` only modify in-memory cache and mark dirty. `flush_all()` is called once in controller post-processing (before terminal SSE event) to persist the final snapshot. Consequences: (1) `ArtifactVersion` numbers can be sparse — intermediate in-memory edits are folded into one DB record. (2) `list_artifacts()` merges DB + in-memory cache so engine context sees same-run changes. (3) `ToolResult.metadata` carries `artifact_snapshot` for real-time frontend updates during execution; REST API reflects the latest only after flush. (4) `create_from_upload` bypasses write-back and commits immediately (not in engine loop).

- **History is MessageEvent, not Message fields** (`src/core/event_history.py`): Conversation history for any LLM call is reconstructed from `MessageEvent` records along the conversation path, not from `Message.user_input` / `Message.response` (those are display-only now). At turn start, controller loads all path events into `state["events"]` with `is_historical=True`; the engine appends new events with `is_historical=False`. `EventHistory` filters by `agent_name`, then scans right-to-left for the most recent boundary (`compaction_summary`, or `subagent_instruction` with `fresh_start=True` for sub-agents) and emits `[boundary_content, ...events_after]` as LLM messages. Only `is_historical=False` events are batch-written at turn end.

- **End turn ≠ block next-turn continuation**: Unrecoverable errors (LLM retry exhausted, compaction failure, etc.) end the current turn loudly (`state["error"]=True`, ERROR event), but events persist unconditionally regardless of `has_error`. The next user message's `path_events` includes the failed turn's events, `EventHistory` skips `success=False` markers, and the LLM continues from where it left off — unless the user explicitly retries or branches. Corollary: don't pack partial `response_content` into `state["response"]` on engine error paths — `Message.response` is a display-only snapshot decoupled from history; the full `llm_complete` content is already in events. See `docs/architecture/engine.md` → *Design Decisions → 错误处理* for the full rationale.

- **In-engine compaction** (`src/core/compaction_runner.py`): Compaction runs inside the engine loop after every LLM call. If `input_tokens + output_tokens > COMPACTION_TOKEN_THRESHOLD`, a `compaction_start` event is appended to `state["events"]` (persisted, surfaces the trigger inputs for replay / audit), then `compact_agent` produces a single structured summary which is appended as a `compaction_summary` `ExecutionEvent` to the tail (tagged with the triggering agent's name). `EventHistory`'s right-to-left scan stops at the summary → everything before it is invisible to subsequent LLM calls for that agent. Semantics:
  1. **Summary is the sole memory of compacted events** — the agent cannot see the original `llm_complete` / `tool_complete` text. `compact_agent` prompt's *Current Work* / *Next Step* sections must carry in-flight state (e.g. "I just called tool X, result pending") so tool results appended after the summary make sense.
  2. **Compaction does NOT backstop tool-result overflow.** A tool returning a huge blob *after* compaction (same turn) can still push the next LLM call past model limits. This is by design: generic truncation cannot semantically summarize structured tool output without corrupting agent-relevant data. Tool authors own output-size discipline — `max_length` / pagination / domain-aware truncation with an explicit `(truncated at N)` marker. If a tool does overflow, the LLM call fails loudly (ERROR to user) rather than silently losing data.
  3. **Per-agent isolation.** `compaction_summary` is tagged with `agent_name`; `EventHistory` filters before scanning, so lead and sub-agent compactions are independent. Sub-agent history also respects `subagent_instruction.fresh_start=True` as an additional boundary for per-session isolation.
  4. **Compaction LLM failure → loud fail.** `astream_with_retry` already provides the same retry as any other agent LLM call; if it still throws, `compaction_runner` appends a `compaction_summary` event with `success=False` (paired with the `compaction_start` so the event stream stays well-formed for replay / UI), then re-raises. `EventHistory` ignores `success=False` summaries entirely — neither boundary nor message — so no in-flight context is silently amputated. Engine catches the exception at the `maybe_trigger` call site and marks the turn ERROR (mirrors `_call_llm` failure handling). No placeholder-summary path: inserting an "equivalent to hard truncation" boundary mid-turn would erase the just-emitted `llm_complete` and orphan the next `tool_complete`, leaving the model with no way to interpret a tool result it never asked for.

## Testing

- pytest config: `testpaths = ["tests"]`, default `test_*.py` collection pattern
- `tests/manual/` contains scripts that require external services (API keys, running server, Redis, etc.) — **file names must NOT start with `test_`** to avoid pytest auto-collection (e.g. `coalescer_bench.py`, `api_smoke.py`)

## Code Conventions

- Tool calls use XML format with CDATA for all parameter values (`<![CDATA[...]]>`), parsed via `xml.etree.ElementTree`
- Tools must return `ToolResult` dataclass; agents must return `AgentResponse` dataclass
- Use unified `StreamEventType` from `core/events.py` for all streaming event layers
- All protected API endpoints use `Depends(get_current_user)`; admin endpoints use `Depends(require_admin)`
- **ORM instances are short-lived persistence snapshots, not runtime state containers.** In async sessions, ORM attribute access on expired instances triggers implicit IO → `MissingGreenlet`. Rules:
  - Timestamps: `server_default=func.now()` for creation, `onupdate=func.now()` for updates. Do not assign `datetime.now()` in repository code.
  - Prefer ORM attribute mutation when the row is already dirty (e.g. changing `active_branch` lets `onupdate` handle `updated_at` automatically).
  - Use bulk `UPDATE` only when the row has no other attribute change but needs a DB-side value written (e.g. `update_response` bumping `conversation.updated_at` via `func.now()`).
  - Never assign SQL expressions (e.g. `func.now()`) directly to ORM instance attributes; use bulk UPDATE instead.
  - After bulk UPDATE or commit, treat same-session ORM instances of affected rows as potentially stale — use explicit `refresh()` or a fresh query to read current values.
