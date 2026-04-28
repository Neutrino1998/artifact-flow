'use client';

import { useCallback } from 'react';
import { useStreamStore, scheduleContentUpdate } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useUIStore } from '@/stores/uiStore';
import { connectSSE } from '@/lib/sse';
import { StreamEventType } from '@/types/events';
import type { SSEEvent } from '@/types/events';
import * as api from '@/lib/api';

const ARTIFACT_TOOLS = new Set([
  'create_artifact',
  'update_artifact',
  'rewrite_artifact',
]);

// Module-level AbortController shared across all useSSE() instances.
// This ensures that PermissionModal.connect() and useChat.disconnect()
// operate on the same controller, preventing orphaned SSE connections.
let _sharedAbortController: AbortController | null = null;

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
  const endStream = useStreamStore((s) => s.endStream);
  const pushNonAgentBlock = useStreamStore((s) => s.pushNonAgentBlock);
  const updateNonAgentBlock = useStreamStore((s) => s.updateNonAgentBlock);
  const setExecutionMetrics = useStreamStore((s) => s.setExecutionMetrics);
  const setCancelled = useStreamStore((s) => s.setCancelled);
  const setReconnecting = useStreamStore((s) => s.setReconnecting);

  // Conversation store actions
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);

  // Artifact store
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactCurrent = useArtifactStore((s) => s.setCurrent);
  const setArtifactVersions = useArtifactStore((s) => s.setVersions);
  const setSelectedVersion = useArtifactStore((s) => s.setSelectedVersion);
  const addPendingUpdate = useArtifactStore((s) => s.addPendingUpdate);
  const clearPendingUpdates = useArtifactStore((s) => s.clearPendingUpdates);

  // UI store
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const refreshAfterComplete = useCallback(
    async (conversationId: string) => {
      try {
        const [detail, list] = await Promise.all([
          api.getConversation(conversationId, { force: true }),
          api.listConversations(20, 0),
        ]);
        // Only update current conversation if user is still viewing it
        // (or viewing no conversation, i.e. the new-conversation flow)
        const viewing = useConversationStore.getState().current?.id;
        if (!viewing || viewing === conversationId) {
          setCurrent(detail);
        }
        setConversations(list.conversations, list.total, list.has_more);
        clearPendingUpdates();
        // Refresh artifact list unconditionally — user may have navigated back
        // to the list view mid-stream, so `current` being null does NOT mean
        // the list is irrelevant.
        api.listArtifacts(conversationId)
          .then((artList) => setArtifacts(artList.artifacts))
          .catch(() => {});
        // Refresh detail only if user is still viewing one.
        const curArtifact = useArtifactStore.getState().current;
        if (curArtifact) {
          api.getArtifact(conversationId, curArtifact.id).then((artDetail) => {
            setArtifactCurrent(artDetail);
            setArtifactVersions(artDetail.versions);
            setSelectedVersion(null);
          }).catch(() => {});
        }
      } catch (err) {
        console.error('Failed to refresh after complete:', err);
      }
    },
    [setCurrent, setConversations, clearPendingUpdates, setArtifactCurrent, setArtifacts, setArtifactVersions, setSelectedVersion]
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
          const finalContent = data?.content as string | undefined;

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

          const tokenUsage = data?.token_usage as { input_tokens: number; output_tokens: number; total_tokens: number } | undefined;
          const model = data?.model as string | undefined;
          const durationMs = data?.duration_ms as number | undefined;

          updateCurrentSegment({
            ...(finalContent ? { content: finalContent } : {}),
            isThinking: false,
            ...llmOutputUpdate,
            ...(tokenUsage ? { tokenUsage } : {}),
            ...(model ? { model } : {}),
            ...(durationMs != null ? { llmDurationMs: durationMs } : {}),
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

          addToolCallToSegment({
            id: `${toolName}-${Date.now()}`,
            toolName,
            params,
            agent,
            status: 'running',
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
              api.getArtifact(sessionId, artifactId).then((detail) => {
                // Discard out-of-order responses: the in-memory cache only ever
                // advances, so a response with current_version <= what we've
                // already applied was issued before a newer one we kept.
                const cur = useArtifactStore.getState().current;
                if (
                  cur?.id === detail.id &&
                  cur.current_version > detail.current_version
                ) return;
                setArtifactCurrent(detail);
                setArtifactVersions(detail.versions);
                setSelectedVersion(null);
              }).catch(() => {});
            }
          }
          break;
        }

        case StreamEventType.PERMISSION_REQUEST:
          setPermissionRequest({
            toolName: data?.tool as string ?? '',
            params: data?.params as Record<string, unknown> ?? {},
          });
          break;

        case StreamEventType.PERMISSION_RESULT:
          setPermissionRequest(null);
          break;

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
          refreshAfterComplete(conversationId);
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
          refreshAfterComplete(conversationId);
          break;
        }

        case StreamEventType.ERROR: {
          setError(data?.error as string ?? 'Unknown error');
          const errMsgId = useStreamStore.getState().messageId;
          if (errMsgId) {
            snapshotSegments(errMsgId);
          }
          endStream();
          refreshAfterComplete(conversationId);
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
      addPendingUpdate, setArtifactSessionId, setArtifactCurrent, setArtifacts,
      setArtifactVersions, setSelectedVersion,
      pushNonAgentBlock, updateNonAgentBlock, setExecutionMetrics, setCancelled,
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
                  endStream();
                  refreshAfterComplete(conversationId);
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
      endStream();
      refreshAfterComplete(conversationId);
    },
    [handleEvent, endStream, setReconnecting, refreshAfterComplete],
  );

  const connect = useCallback(
    (streamUrl: string, conversationId: string, _messageId: string) => {
      if (_sharedAbortController) {
        _sharedAbortController.abort();
      }

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
    [handleEvent, setError, endStream, setReconnecting, attemptReconnect],
  );

  const disconnect = useCallback(() => {
    if (_sharedAbortController) {
      _sharedAbortController.abort();
      _sharedAbortController = null;
    }
    endStream();
  }, [endStream]);

  return { connect, disconnect };
}
