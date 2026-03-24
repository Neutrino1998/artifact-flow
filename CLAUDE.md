# CLAUDE.md

## Project Overview

ArtifactFlow is a multi-agent system with a [Pi-style](https://github.com/badlogic/pi-mono) execution engine. It uses a dual-artifact architecture (Task Plan Artifact + Result Artifact) with specialized AI agents (Lead, Search, Crawl) that collaborate to perform tasks.

**Requirements:** Python 3.10+

## Essential Commands

```bash
# JWT secret (required, server won't start without it)
echo "ARTIFACTFLOW_JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env

# API type sync (after changing backend API schemas)
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

- **Transaction ownership**: `DatabaseManager.session()` only manages lifecycle (create + close). Transaction control (`flush` + `commit`) is in Repository methods to keep write locks short.

- **Event sourcing**: All execution events are append-only persisted to `MessageEvent` table. `llm_chunk` is SSE-only (streaming transport), NOT persisted — `llm_complete` has the full content.

- **SSE transport**: Frontend uses `fetch` + `ReadableStream` (not `EventSource`) because EventSource cannot send custom `Authorization` headers.

- **Security: 404 not 403**: Cross-user access returns 404 to avoid leaking resource existence. Auth stays at API boundary only — core/engine/tools receive `user_id` as a plain field.

- **Permission interrupts**: `CONFIRM`-level tools trigger `TaskManager.create_interrupt()` which blocks on `asyncio.Event`. Timeout and client disconnect both treated as deny. Multi-tool turns execute serially so interrupts naturally slot between tools.

- **Three-layer responsibility model**:
  - **Repository** (`src/repositories/`): Pure data access — returns ORM objects, no formatting/serialization/business logic. ORM objects must not escape the session that loaded them.
  - **Manager** (`src/core/conversation_manager.py`, `src/tools/builtin/artifact_ops.py`): Use-case orchestration — ownership checks, history formatting, artifact write-back, serialization to dicts. Routers must not bypass Manager to call Repo directly.
  - **Router** (`src/api/routers/`): Transport layer — auth, parameter parsing, HTTP mapping. No business logic, no Repo imports.

- **Artifact write-back** (`ArtifactManager`): During engine execution, `create_artifact`/`update_artifact`/`rewrite_artifact` only modify in-memory cache and mark dirty. `flush_all()` is called once in controller post-processing (before terminal SSE event) to persist the final snapshot. Consequences: (1) `ArtifactVersion` numbers can be sparse — intermediate in-memory edits are folded into one DB record. (2) `list_artifacts()` merges DB + in-memory cache so engine context sees same-run changes. (3) `ToolResult.metadata` carries `artifact_snapshot` for real-time frontend updates during execution; REST API reflects the latest only after flush. (4) `create_from_upload` bypasses write-back and commits immediately (not in engine loop).

## Code Conventions

- Tool calls use XML format with CDATA for all parameter values (`<![CDATA[...]]>`), parsed via `xml.etree.ElementTree`
- Tools must return `ToolResult` dataclass; agents must return `AgentResponse` dataclass
- Use unified `StreamEventType` from `core/events.py` for all streaming event layers
- All protected API endpoints use `Depends(get_current_user)`; admin endpoints use `Depends(require_admin)`
