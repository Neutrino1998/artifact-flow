'use client';

import { useCallback } from 'react';
import { useStreamStore, scheduleContentUpdate } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useUIStore } from '@/stores/uiStore';
import { connectSSE } from '@/lib/sse';
import { StreamEventType } from '@/types/events';
import type { SSEEvent, LLMCompleteData } from '@/types/events';
import * as api from '@/lib/api';
import { refreshArtifactList } from '@/lib/refreshArtifactList';
import { autoOpenArtifact } from '@/lib/artifactAutoOpen';
import { bumpArtifactFetchGen } from '@/lib/artifactFetchGen';
import { getNavGen } from '@/lib/navGen';

const ARTIFACT_TOOLS = new Set([
  'create_artifact',
  'update_artifact',
  'rewrite_artifact',
]);

// Module-level AbortController shared across all useSSE() instances.
// This ensures that PermissionModal.connect() and useChat.disconnect()
// operate on the same controller, preventing orphaned SSE connections.
let _sharedAbortController: AbortController | null = null;

// Most recent permission_result, latched until the next tool_start consumes
// it. Engine guarantees serial execution, so the immediately-following
// tool_start is the one this result belongs to (matches reconstructSegments
// pairing). Cleared on stream start/end via endStream() consumer.
let _pendingPermissionResult: { approved: boolean; reason?: string } | null = null;

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BASE_DELAY_MS = 1000;

export function useSSE() {

  // Stream store actions
  const pushSegment = useStreamStore((s) => s.pushSegment);
  const updateCurrentSegment = useStreamStore((s) => s.updateCurrentSegment);
  const addToolCallToSegment = useStreamStore((s) => s.addToolCallToSegment);
  const updateToolCallInSegment = useStreamStore((s) => s.updateToolCallInSegment);
  const snapshotSegments = useStreamStore((s) => s.snapshotSegments);
  const setPermissionRequest = useStreamStore((s) => s.setPermissionRequest);
  const setError = useStreamStore((s) => s.setError);
  const startStream = useStreamStore((s) => s.startStream);
  const endStream = useStreamStore((s) => s.endStream);
  const pushNonAgentBlock = useStreamStore((s) => s.pushNonAgentBlock);
  const updateNonAgentBlock = useStreamStore((s) => s.updateNonAgentBlock);
  const setExecutionMetrics = useStreamStore((s) => s.setExecutionMetrics);
  const setCancelled = useStreamStore((s) => s.setCancelled);
  const setReconnecting = useStreamStore((s) => s.setReconnecting);
  const setQueuedInfo = useStreamStore((s) => s.setQueuedInfo);

  // Conversation store actions
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);
  const clearConversationActiveIfMatch = useConversationStore((s) => s.clearConversationActiveIfMatch);

  // Artifact store
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactCurrent = useArtifactStore((s) => s.setCurrent);
  const setArtifactCurrentAuto = useArtifactStore((s) => s.setCurrentAuto);
  const refreshArtifactCurrent = useArtifactStore((s) => s.refreshCurrent);
  const setArtifactVersions = useArtifactStore((s) => s.setVersions);
  const setSelectedVersion = useArtifactStore((s) => s.setSelectedVersion);
  const addPendingUpdate = useArtifactStore((s) => s.addPendingUpdate);
  const clearPendingUpdates = useArtifactStore((s) => s.clearPendingUpdates);

  // UI store
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const refreshAfterComplete = useCallback(
    async (conversationId: string, terminalMessageId: string | null) => {
      // Capture nav-gen BEFORE the await. Both startNewChat() and
      // switchConversation() bump this synchronously on entry, so any
      // navigation event during our await leaves myNavGen != getNavGen().
      // This is the only reliable "user navigated away" signal because
      // current?.id is null in three indistinguishable scenarios:
      //   - first-message new conv (legit, should populate)
      //   - startNewChat (user explicitly left, MUST NOT populate)
      //   - switchConversation handoff (user picked another conv, MUST NOT
      //     populate — would briefly revive abandoned conv before the new
      //     setCurrent overwrites)
      const myNavGen = getNavGen();
      // Stream just ended — invalidate every in-flight auto-open fetch
      // unconditionally. Cases this catches that the per-revert bump did
      // not: the FIRST auto-open from this stream hasn't resolved yet, so
      // store.current is still null and the auto-selected branch below
      // wouldn't fire. Without this bump, the late callback would
      // resurrect the panel after stream end. Costs nothing if there are
      // no fetches outstanding.
      bumpArtifactFetchGen();
      // Compare-and-clear the sidebar dot for *this* terminal's message_id
      // only. If the user already kicked off a new turn on the same conv,
      // its sendMessage() has set active_message_id to the new id, and this
      // call is a no-op — the new turn's mark survives untouched. The
      // backend list refresh below will eventually sync truth either way.
      if (terminalMessageId) {
        clearConversationActiveIfMatch(conversationId, terminalMessageId);
      }
      try {
        const [detail, list] = await Promise.all([
          api.getConversation(conversationId, { force: true }),
          api.listConversations(20, 0),
        ]);
        // Sidebar list refresh is harmless cross-conversation, so always apply.
        // The backend list endpoint reads active_message_id from the lease
        // store, which is the single source of truth for execution state, so
        // setConversations(...) restores the authoritative view (covers cross-
        // tab/device + the race window where this tab's optimistic write was
        // staler than the server view). No defensive second write needed.
        setConversations(list.conversations, list.total, list.has_more);

        // Everything below mutates state that belongs to "the conversation
        // the user is on". A nav-gen change means they aren't on this conv
        // anymore — drop the whole detail/artifact write path.
        if (myNavGen !== getNavGen()) return;

        setCurrent(detail);
        clearPendingUpdates();

        // Artifact refresh is further gated by artifactStore.sessionId so
        // that conversations without artifact tools don't trigger a useless
        // GET. Nav-gen guard above already ensured we're still on this conv.
        const artifactSession = useArtifactStore.getState().sessionId;
        const ownsArtifactSession = artifactSession === conversationId;

        if (ownsArtifactSession) {
          refreshArtifactList(
            conversationId,
            setArtifacts,
            setArtifactSessionId,
            () => useArtifactStore.getState().sessionId,
          );
        }
        const { current: curArtifact, autoSelected } = useArtifactStore.getState();
        if (curArtifact && ownsArtifactSession) {
          if (autoSelected) {
            // Stream finished and the panel is on an artifact the agent
            // auto-opened — revert to list so the user sees the overview.
            // The list refresh above already loaded the latest artifacts.
            // (The unconditional bump at the top of this function has
            // already invalidated any in-flight auto-opens.)
            setArtifactCurrent(null);
          } else {
            // User actively picked this artifact — refresh content but keep
            // them on it.
            api.getArtifact(conversationId, curArtifact.id).then((artDetail) => {
              // Re-check at resolution: another nav could have fired during
              // this nested await.
              if (myNavGen !== getNavGen()) return;
              setArtifactCurrent(artDetail);
              setArtifactVersions(artDetail.versions);
              setSelectedVersion(null);
            }).catch(() => {});
          }
        }
      } catch (err) {
        console.error('Failed to refresh after complete:', err);
      }
    },
    [setCurrent, setConversations, clearConversationActiveIfMatch, clearPendingUpdates, setArtifactCurrent, setArtifacts, setArtifactSessionId, setArtifactVersions, setSelectedVersion]
  );

  const handleEvent = useCallback(
    (event: SSEEvent, conversationId: string) => {
      const { type, data } = event;

      switch (type) {
        case StreamEventType.METADATA: {
          // Dev-only consistency check: verify message_id from metadata matches streamStore
          if (process.env.NODE_ENV === 'development') {
            const metaMsgId = data?.message_id as string | undefined;
            const store = useStreamStore.getState();
            if (metaMsgId && store.messageId && metaMsgId !== store.messageId) {
              console.warn('[SSE] message_id mismatch:', { meta: metaMsgId, store: store.messageId });
            }
          }
          break;
        }

        case StreamEventType.AGENT_START: {
          // Mark previous segment as complete — a new turn implies the prior is done
          updateCurrentSegment({ status: 'complete' });
          pushSegment(event.agent ?? 'Agent');
          // Engine started executing — clear the concurrency-queue banner if any.
          setQueuedInfo(null);
          break;
        }

        case StreamEventType.EXECUTION_QUEUED: {
          const ahead = data?.ahead as number | undefined;
          const maxConcurrent = data?.max_concurrent as number | undefined;
          setQueuedInfo({
            ahead: ahead ?? 0,
            maxConcurrent: maxConcurrent ?? 0,
          });
          break;
        }

        case StreamEventType.LLM_CHUNK: {
          const reasoning = data?.reasoning_content as string | undefined;
          if (reasoning !== undefined) {
            updateCurrentSegment({ reasoningContent: reasoning, isThinking: true });
          }

          const content = data?.content as string | undefined;
          if (content !== undefined) {
            // Auto-fold thinking when content starts arriving
            const currentSeg = useStreamStore.getState().segments;
            const last = currentSeg[currentSeg.length - 1];
            if (last?.isThinking) {
              updateCurrentSegment({ isThinking: false });
            }
            // Use RAF-throttled update for segment content
            scheduleContentUpdate(content);
          }
          break;
        }

        case StreamEventType.LLM_COMPLETE: {
          const d = (data ?? {}) as Partial<LLMCompleteData>;
          const finalContent = d.content;

          // Determine the definitive content (from event data or accumulated chunks)
          const segs = useStreamStore.getState().segments;
          const lastSeg = segs[segs.length - 1];
          const effectiveContent = finalContent || lastSeg?.content || '';

          // Preserve raw LLM output when it contains XML tool calls.
          // This covers call_subagent (no TOOL_START event) and acts as
          // an early save for regular tools (TOOL_START won't overwrite).
          const llmOutputUpdate = effectiveContent.includes('<tool_call>') && !lastSeg?.llmOutput
            ? { llmOutput: effectiveContent }
            : {};

          updateCurrentSegment({
            ...(finalContent ? { content: finalContent } : {}),
            isThinking: false,
            ...llmOutputUpdate,
            // Backfill reasoning when the provider only delivers it on the
            // final event (no llm_chunk reasoning_content stream). Without
            // this, live shows blank reasoning while replay can — same gap
            // as P2 in the reviewer's findings.
            ...(d.reasoning_content ? { reasoningContent: d.reasoning_content } : {}),
            ...(d.token_usage ? { tokenUsage: d.token_usage } : {}),
            ...(d.model ? { model: d.model } : {}),
            ...(d.duration_ms != null ? { llmDurationMs: d.duration_ms } : {}),
          });
          break;
        }

        case StreamEventType.AGENT_COMPLETE:
          updateCurrentSegment({ status: 'complete' });
          break;

        case StreamEventType.TOOL_START: {
          const toolName = data?.tool as string ?? '';
          const params = data?.params as Record<string, unknown> ?? {};
          const agent = event.agent ?? '';

          // Preserve LLM output before clearing content (only on first tool_start)
          const segs = useStreamStore.getState().segments;
          const lastSeg = segs[segs.length - 1];
          const preserveLlmOutput = lastSeg?.content && !lastSeg.llmOutput
            ? { llmOutput: lastSeg.content }
            : {};

          // Latch a just-resolved permission_result onto this tool call (the
          // tool the user just approved/denied). Engine emits permission_result
          // immediately before this tool_start.
          const permission = _pendingPermissionResult ?? undefined;
          _pendingPermissionResult = null;

          addToolCallToSegment({
            id: `${toolName}-${Date.now()}`,
            toolName,
            params,
            agent,
            status: 'running',
            ...(permission ? { permission } : {}),
          });
          // Clear streaming content when entering tool phase
          updateCurrentSegment({ content: '', ...preserveLlmOutput });
          break;
        }

        case StreamEventType.TOOL_COMPLETE: {
          const toolName = data?.tool as string ?? '';
          const success = data?.success as boolean ?? true;
          const result = typeof data?.result_data === 'string'
            ? data.result_data as string
            : !success && typeof data?.error === 'string'
              ? data.error as string
              : JSON.stringify(data?.result_data ?? data?.result ?? '');
          const durationMs = data?.duration_ms as number | undefined;

          // Find the matching running tool call across all segments. The engine
          // guarantees a paired TOOL_START precedes every TOOL_COMPLETE (see
          // engine.py _execute_tools), so a missing match means the producer
          // contract is broken — surface it loudly instead of silently dropping.
          const segments = useStreamStore.getState().segments;
          let runningId: string | undefined;
          for (const seg of segments) {
            const running = seg.toolCalls.find(
              (tc) => tc.toolName === toolName && tc.status === 'running'
            );
            if (running) {
              runningId = running.id;
              break;
            }
          }
          if (!runningId) {
            console.error(
              `[useSSE] tool_complete for "${toolName}" with no matching running tool — engine pairing contract violated`
            );
          } else {
            updateToolCallInSegment(runningId, {
              status: success ? 'success' : 'error',
              result,
              durationMs,
            });
          }

          // Auto-open artifact panel and update content on artifact tool completion.
          // REST overlays in-memory cache via ArtifactManager.get_active(), so
          // GET returns the just-written content even before flush_all.
          if (ARTIFACT_TOOLS.has(toolName) && success) {
            setArtifactPanelVisible(true);
            const params = data?.params as Record<string, unknown> | undefined;
            const artifactId = params?.id as string | undefined;
            if (artifactId) {
              addPendingUpdate(artifactId);
              const sessionId = conversationId;
              setArtifactSessionId(sessionId);
              setSelectedVersion(null);
              // All the race/ownership logic (per-fetch gen, same-id refresh,
              // cross-id ownership) lives in `autoOpenArtifact` — see that
              // helper for the full decision matrix and its unit tests.
              autoOpenArtifact(sessionId, artifactId, {
                getCurrent: () => useArtifactStore.getState().current,
                getAutoSelected: () => useArtifactStore.getState().autoSelected,
                setCurrentAuto: setArtifactCurrentAuto,
                refreshCurrent: refreshArtifactCurrent,
                setVersions: setArtifactVersions,
                setSelectedVersion: setSelectedVersion,
              });
            }
          }

          // Refresh the artifact LIST whenever an artifact has been created or
          // mutated in this tool turn. Two trigger sources:
          //   (1) Explicit artifact tools (create / update / rewrite) — fixes
          //       a pre-existing gap where new agent artifacts didn't appear
          //       in the list view until stream complete.
          //   (2) Auto-persist middleware — tool result was saved as artifact;
          //       result_data carries metadata.persisted_artifact_id.
          // The REST endpoint overlays the active manager's in-memory cache,
          // so the new entry shows up before flush_all has run.
          // Guarded refresh: rapid back-to-back tool completions in one turn
          // can otherwise produce out-of-order responses overwriting newer
          // state with older snapshots.
          const metadata = data?.metadata as Record<string, unknown> | undefined;
          const persistedId = metadata?.persisted_artifact_id as string | undefined;
          if (success && (ARTIFACT_TOOLS.has(toolName) || persistedId)) {
            // refreshArtifactList handles the session-id stamping internally
            // (claim-before-await), so no separate setArtifactSessionId here.
            refreshArtifactList(
              conversationId,
              setArtifacts,
              setArtifactSessionId,
              () => useArtifactStore.getState().sessionId,
            );
          }
          break;
        }

        case StreamEventType.PERMISSION_REQUEST:
          setPermissionRequest({
            toolName: data?.tool as string ?? '',
            params: data?.params as Record<string, unknown> ?? {},
          });
          break;

        case StreamEventType.PERMISSION_RESULT: {
          setPermissionRequest(null);
          const approved = (data?.approved as boolean) ?? false;
          const reason = data?.reason as string | undefined;
          _pendingPermissionResult = reason ? { approved, reason } : { approved };
          break;
        }

        case StreamEventType.QUEUED_MESSAGE:
          pushNonAgentBlock({
            kind: 'inject',
            id: `inject-${Date.now()}`,
            content: data?.content as string ?? '',
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          break;

        case StreamEventType.COMPACTION_START: {
          const d = data as import('@/types/events').CompactionStartData | undefined;
          pushNonAgentBlock({
            kind: 'compaction',
            id: `compact-${event.timestamp}`,
            state: 'running',
            triggerTokens: d
              ? { input: d.last_input_tokens, output: d.last_output_tokens }
              : undefined,
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          break;
        }

        case StreamEventType.COMPACTION_SUMMARY: {
          // Find the most recent running compaction block and transition it
          // to done (or error) with summary + stats. compaction_start and
          // compaction_summary are paired by order of arrival; we don't have an
          // explicit correlation id, so the most-recent-running match works.
          const d = data as import('@/types/events').CompactionSummaryData | undefined;
          if (!d) break;
          const blocks = useStreamStore.getState().nonAgentBlocks;
          const target = [...blocks].reverse().find(
            (b): b is import('@/stores/streamStore').CompactionBlock =>
              b.kind === 'compaction' && b.state === 'running'
          );
          if (target) {
            updateNonAgentBlock(target.id, {
              state: d.error ? 'error' : 'done',
              summary: d.content,
              model: d.model,
              tokenUsage: d.token_usage,
              durationMs: d.duration_ms,
              error: d.error,
            });
          }
          break;
        }

        case StreamEventType.CANCELLED: {
          const metrics = data?.execution_metrics;
          if (metrics) setExecutionMetrics(metrics as import('@/types/events').ExecutionMetrics);
          const messageId = useStreamStore.getState().messageId;
          if (messageId) {
            snapshotSegments(messageId);
          }
          setCancelled(true);
          endStream();
          refreshAfterComplete(conversationId, messageId);
          break;
        }

        case StreamEventType.COMPLETE: {
          const metrics = data?.execution_metrics;
          if (metrics) setExecutionMetrics(metrics as import('@/types/events').ExecutionMetrics);
          const messageId = useStreamStore.getState().messageId;
          if (messageId) {
            snapshotSegments(messageId);
          }
          endStream();
          refreshAfterComplete(conversationId, messageId);
          break;
        }

        case StreamEventType.ERROR: {
          const errMsg = (data?.error as string) ?? 'Unknown error';
          // Push as a flow block FIRST so snapshotSegments captures it into
          // completedNonAgentBlocks. Without this, AssistantMessage's
          // lazy-load gate (completedSegs !== undefined → skip refetch)
          // hides the just-finished failure as a green "Completed" until
          // the page is reloaded — the live/replay regression P1.
          pushNonAgentBlock({
            kind: 'error',
            id: `error-${event.timestamp}`,
            error: errMsg,
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          setError(errMsg);
          const errMsgId = useStreamStore.getState().messageId;
          if (errMsgId) {
            snapshotSegments(errMsgId);
          }
          endStream();
          refreshAfterComplete(conversationId, errMsgId);
          break;
        }

        default:
          console.warn('Unhandled SSE event type:', type);
      }
    },
    [
      pushSegment, updateCurrentSegment, addToolCallToSegment,
      updateToolCallInSegment, snapshotSegments, setPermissionRequest,
      setError, endStream, refreshAfterComplete, setArtifactPanelVisible,
      addPendingUpdate, setArtifactSessionId, setArtifactCurrent,
      setArtifactCurrentAuto, refreshArtifactCurrent, setArtifacts,
      setArtifactVersions, setSelectedVersion,
      pushNonAgentBlock, updateNonAgentBlock, setExecutionMetrics, setCancelled,
      setQueuedInfo,
    ]
  );

  const attemptReconnect = useCallback(
    async (
      conversationId: string,
      lastEventId: string | null,
      ownerController: AbortController,
      startAttempt = 0,
    ) => {
      for (let attempt = startAttempt; attempt < MAX_RECONNECT_ATTEMPTS; attempt++) {
        const delay = RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt);
        await new Promise((r) => setTimeout(r, delay));

        // Bail out if ownership has changed (user started a new stream or disconnected)
        if (_sharedAbortController !== ownerController || ownerController.signal.aborted) return;

        try {
          const active = await api.getActiveStream(conversationId);
          if (_sharedAbortController !== ownerController || ownerController.signal.aborted) return;

          // Execution still active — reconnect with lastEventId
          setReconnecting(false);

          const controller = new AbortController();
          _sharedAbortController = controller;
          const nextAttempt = attempt + 1;

          let receivedTerminal = false;
          const connection = connectSSE(
            active.stream_url,
            {
              onEvent: (event) => {
                if (controller.signal.aborted) return;
                handleEvent(event, conversationId);
                const t = event.type;
                if (t === 'complete' || t === 'cancelled' || t === 'error') {
                  receivedTerminal = true;
                }
              },
              onError: () => {
                // SSE failed — could be handshake or mid-stream read error.
                // Use connection.lastEventId (not the outer lastEventId) so
                // events already consumed in this attempt aren't replayed.
                if (controller.signal.aborted) return;
                if (nextAttempt < MAX_RECONNECT_ATTEMPTS) {
                  setReconnecting(true);
                  attemptReconnect(conversationId, connection.lastEventId ?? lastEventId, controller, nextAttempt);
                } else {
                  setReconnecting(false);
                  const reconnectMsgId = useStreamStore.getState().messageId;
                  endStream();
                  refreshAfterComplete(conversationId, reconnectMsgId);
                }
              },
              onClose: () => {
                if (receivedTerminal || controller.signal.aborted) return;
                setReconnecting(true);
                attemptReconnect(conversationId, connection.lastEventId, controller);
              },
            },
            controller.signal,
            lastEventId,
          );
          return; // SSE connection initiated (handlers take over)
        } catch {
          // getActiveStream failed (404 or network error) — try next attempt
          continue;
        }
      }

      // All attempts exhausted — execution likely finished
      // Final ownership check before touching shared state
      if (_sharedAbortController !== ownerController) return;
      setReconnecting(false);
      const exhaustedMsgId = useStreamStore.getState().messageId;
      endStream();
      refreshAfterComplete(conversationId, exhaustedMsgId);
    },
    [handleEvent, endStream, setReconnecting, refreshAfterComplete],
  );

  const connect = useCallback(
    (streamUrl: string, conversationId: string, messageId: string) => {
      if (_sharedAbortController) {
        _sharedAbortController.abort();
      }
      // Clear any leaked permission latch from a prior stream — the next
      // tool_start in this stream is unrelated to a previous turn's modal.
      _pendingPermissionResult = null;

      // Enter streaming state. Centralizing this here (rather than relying on
      // every caller to call startStream first) ensures the reconnect path
      // also flips isStreaming/messageId/conversationId, so StreamingMessage
      // renders, MessageInput shows stop/inject, and permission resume works.
      startStream(streamUrl, messageId, conversationId);

      const controller = new AbortController();
      _sharedAbortController = controller;

      let receivedTerminal = false;

      const connection = connectSSE(
        streamUrl,
        {
          onEvent: (event) => {
            if (controller.signal.aborted) return;
            handleEvent(event, conversationId);
            const t = event.type;
            if (t === 'complete' || t === 'cancelled' || t === 'error') {
              receivedTerminal = true;
            }
          },
          onError: (err) => {
            const status = (err as Error & { status?: number }).status;
            // Non-retryable: 401 auth expired, 404 resource not found
            if (status === 401 || status === 404) {
              setError(err.message);
              endStream();
              return;
            }
            // Retryable: 502/503/network error — use same reconnect path as onClose
            setReconnecting(true);
            attemptReconnect(conversationId, connection.lastEventId, controller);
          },
          onClose: () => {
            if (receivedTerminal || controller.signal.aborted) return;
            // Abnormal disconnect — attempt reconnection
            setReconnecting(true);
            attemptReconnect(conversationId, connection.lastEventId, controller);
          },
        },
        controller.signal,
      );
    },
    [handleEvent, setError, endStream, setReconnecting, attemptReconnect, startStream],
  );

  const disconnect = useCallback(() => {
    if (_sharedAbortController) {
      _sharedAbortController.abort();
      _sharedAbortController = null;
    }
    endStream();
  }, [endStream]);

  // Open SSE if the backend still has an active execution for this conversation.
  // Used when the user navigates back to a conversation whose stream we
  // disconnected on the way out — re-attaches to the live tail instead of
  // showing a frozen view of the already-loaded historical events.
  const reconnectIfActive = useCallback(
    async (conversationId: string) => {
      try {
        const active = await api.getActiveStream(conversationId);
        // The probe is async and switchConversation can fire several in
        // quick succession (e.g. B → C). A late-resolving probe for B must
        // not steal the SSE connection from the now-active C.
        if (useConversationStore.getState().current?.id !== conversationId) return;
        connect(active.stream_url, conversationId, active.message_id);
      } catch {
        // 404 (no active execution) / 410 (stream expired) — nothing live to attach to
      }
    },
    [connect]
  );

  return { connect, disconnect, reconnectIfActive };
}
