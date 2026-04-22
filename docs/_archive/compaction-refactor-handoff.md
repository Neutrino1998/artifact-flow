# Compaction Refactor — Docs Update Handoff

This note is a handoff to the next session. **Task**: scan the repo's
non-archive documentation (`docs/architecture/*`, `docs/*.md`, any inline
design docs) and update anything that still describes the **pre-refactor**
compaction / history behavior. I have not pre-identified which docs need
updating — that's your first pass.

## What changed in this session

The compaction and conversation-history layers were rewritten end to end.
Fifteen commits, from `bbbb9f6` to `896e9b0` (inclusive). High-level shape
of the change:

### Backend

- **History is now MessageEvent, not Message fields.** Conversation history
  is reconstructed from `MessageEvent` records along the path; the old
  `Message.user_input_summary` / `Message.response_summary` fields were
  removed. At turn start the controller loads all path events into
  `state["events"]` with `is_historical=True`; engine appends new events
  with `is_historical=False`.

- **New `core/event_history.py`**: `build_event_history(events, agent_name)`
  filters by agent, scans right-to-left for the most recent boundary
  (`compaction_summary`, or `subagent_instruction` with `fresh_start=True`
  for sub-agents), and returns LLM-ready messages. Replaces the old
  `ConversationManager.format_conversation_history_async` + ContextManager
  `_build_tool_interactions` pair.

- **Compaction moved in-engine.** `core/compaction.py` (CompactionManager)
  is gone. New `core/compaction_runner.py::CompactionRunner` runs
  synchronously inside `engine.execute_loop`: after each LLM call, if
  `input_tokens + output_tokens > COMPACTION_TOKEN_THRESHOLD`, the
  `compact_agent` produces a structured summary and a
  `compaction_summary` `ExecutionEvent` is appended to `state["events"]`
  tail. `EventHistory`'s right-to-left scan stops there → everything
  before is invisible to subsequent LLM calls for that agent.
  - No more async background task / distributed lock / heartbeat / wait.
  - No preserve window (simplifies semantics; next-turn first LLM call
    automatically sees a compacted history if needed).
  - Failure fallback is a placeholder summary at the same tail position.
  - `compaction_start` + `compaction_summary` are both persisted events
    now (earlier intermediate state had compaction_start as SSE-only).

- **Manual compact endpoint removed.** `POST /api/v1/chat/{conv_id}/compact`
  and `get_compaction_manager` dependency gone.

- **Persistence failure is no longer silent.** `_persist_events` returns
  bool; controller converts terminal to ERROR when persistence fails,
  skipping the `Message.response` update to keep history + display
  consistent on abort.

- **`call_subagent` gained `fresh_start`** (default True). The flag goes
  into the SUBAGENT_INSTRUCTION event's data; `EventHistory` uses it as
  a sub-agent history boundary (separate from compaction_summary).

- **`compact_agent` prompt evolved a lot over the session.** Final form:
  7 numbered sections (Primary Request / Artifacts / Tool Interactions /
  Errors / Pending Tasks / Current Work / Next Step), no `<summary>` or
  other outer wrapper (we do not parse — whole LLM response is persisted
  as-is), verbatim quotes enforced via `"..."` quotation marks with
  MUST-level language. Model flipped from `qwen3.6-plus-no-thinking` to
  `qwen3.6-plus` (thinking variant, same as lead). Section 6
  ("Current Work") explicitly notes the pending tool call (compaction
  fires before tool execution).

- **Frame prefix on summary content.** The persisted `content` is
  `"[Prior conversation has been compacted into this summary. Treat it
  as your memory of earlier context and continue from here.]\n\n" +
  <actual summary>`. Parallels the queued_message framing.

- **Config cleaned up.** Removed `COMPACTION_PRESERVE_PAIRS`,
  `COMPACTION_TIMEOUT` (background task), `CONTEXT_MAX_TOKENS`,
  `TRUNCATION_PRESERVE_AI_MSGS`. Retained `COMPACTION_TOKEN_THRESHOLD`.
  (`ContextManager.truncate_messages` and supporting helpers also deleted
  — no fallback truncation path anymore; compaction handles it.)

- **Message table.** `user_input_summary` / `response_summary` removed.
  User indicated dev-phase — no migration written; existing DBs need
  to be rebuilt from the ORM.

### Frontend

- **OpenAPI types regenerated** — `/compact` endpoint and the summary
  fields dropped naturally.
- **SSE types updated**: `COMPACTION_WAIT` removed; `COMPACTION_START`
  and `COMPACTION_SUMMARY` added.
- **Store**: `NonAgentBlock` split into discriminated union
  `InjectBlock | CompactionBlock`. `CompactionBlock` tracks
  `state: 'running' | 'done' | 'error'`, `triggerTokens`, `summary`,
  `model`, `tokenUsage`, `durationMs`, `error`. New `updateNonAgentBlock`
  store action.
- **`useSSE`** pairs compaction_start and compaction_summary events by
  arrival order.
- **`reconstructNonAgentBlocks`** rebuilds compaction blocks from DB
  events for replay.
- **New `FlowBlock` base component** for non-agent inline blocks: accent
  border, collapsible header/body, used by both `InjectFlowBlock` and
  `CompactionFlowBlock`.
- **`CompactionFlowBlock` three states**: running (pulsing badge +
  "compressing Nk tokens…"), done (model · in↑ · out↓ · duration,
  matching `AgentSegmentBlock` header format, click to see summary
  markdown), error (red badge + truncation note).
- **`SummaryPopover.tsx` deleted** (showed the old per-message
  summaries that no longer exist).

### Tests

- Deleted `tests/test_compaction.py` (tested deleted CompactionManager).
- Deleted runtime-store owner-key primitive tests (primitives removed).
- Deleted `/compact` endpoint tests.
- New `tests/test_event_history.py` (14 tests), `tests/test_compaction_runner.py`
  (8 tests), `tests/test_controller_persist.py` (6 tests).
- Added `tests/test_engine_execution.py::TestInEngineCompaction` (3 tests)
  for engine→runner wiring — mutation-verified to fail if the wiring call
  in `engine.py` is removed.
- Fixed a pre-existing unrelated test failure
  (`test_detail_includes_versions`) for `latest_version` field removal.

Full suite at HEAD: 351 passed / 26 skipped / 0 failed.

## What's already in CLAUDE.md

`522a2af` updated `CLAUDE.md` with the architectural essentials:
- Transaction-ownership caveat (post_process = 3 independent writes,
  orphan-artifact risk documented as accepted trade-off)
- History-as-MessageEvent design decision
- In-engine compaction semantics (4 sub-points: summary-as-sole-memory,
  no backstop for tool-result overflow, per-agent isolation, placeholder
  on failure)

`CLAUDE.md` is in pretty good shape for this refactor — **don't duplicate
it**. What's likely stale is everything outside CLAUDE.md.

## What's likely stale (please verify, then update)

Candidate surfaces to scan:
- `docs/architecture/*.md` — engine, data-layer, concurrency, observability
- `docs/frontend.md`
- `docs/deployment.md` — any env vars referenced
  (`ARTIFACTFLOW_COMPACTION_PRESERVE_PAIRS` etc. are gone)
- Any mermaid diagrams showing old compaction flow (async task +
  distributed lock)
- Any reference to `CompactionManager`, `format_conversation_history_async`,
  `POST /compact`, `user_input_summary`, `response_summary`, or the
  retired config knobs

Also worth a sanity pass:
- `docs/architecture/engine.md` — if it describes the engine loop, it
  should mention the `maybe_trigger` call site (after `_call_llm`,
  before `_execute_tools`) because the engine wiring test now treats
  that call as an invariant.

## Not stale / out of scope

- `docs/_archive/*` — historical docs, leave alone.
- `docs/_archive/frontend-testing-setup-todo.md` (landed in `73a2e03`)
  — also an archive-note, keep.

## Commit range

```
bbbb9f6 refactor(core): unify history + compaction around MessageEvent
677b7c5 fix(compaction): tail-append summary + surface persist failures
522a2af docs(claude-md): document new history + compaction semantics + known caveats
3d48cce fix(compaction): persist compaction_start event for replay
05d89d2 test: migrate suite to new compaction + event-history design
89405dd test(artifacts): update assertions for removed latest_version field
10cf0d5 test(compaction): fix tautology + add engine wiring integration
fe3ef85 feat(frontend): sync compaction protocol + unify flow-block UI
73a2e03 docs(archive): frontend testing setup TODO
501e5b5 fix(compaction): verbose logs, model in event, memory-aid frame on summary
d03a52d tweak(compaction): trim redundant summary sections + uncap response log
9cb80a0 tweak(compaction): enforce verbatim quotes via <quote> XML tags
c45c631 fix(compaction): drop <summary> wrapper; use thinking model
cb037ea tweak(compaction): swap <quote> XML tags for quotation marks
896e9b0 tweak(compaction): note pending tool result in Current Work
```

Reading the commit messages top to bottom is a decent linear narrative
of the refactor if you want context beyond this note.

## Uncommitted detail to be aware of

`src/config.py` has one local uncommitted change: the user lowered
`COMPACTION_TOKEN_THRESHOLD` from its default for manual dev-time
testing. This isn't meant to ship in docs — if you reference the
config value anywhere, use the committed default.
