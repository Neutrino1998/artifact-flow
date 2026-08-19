'use client';

import { useCallback } from 'react';
import { cancelPendingFlush, scheduleContentUpdate, useStreamStore } from '@/stores/streamStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifactStore } from '@/stores/artifactStore';
import { useUIStore } from '@/stores/uiStore';
import { connectSSE } from '@/lib/sse';
import { StreamEventType } from '@/types/events';
import type { SSEEvent, LLMCompleteData, ToolCallProgressData, ArtifactCreatedData, ArtifactUpdatedData } from '@/types/events';
import * as api from '@/lib/api';
import { notifyTaskTerminal } from '@/lib/taskNotifications';
import { useAuthStore } from '@/stores/authStore';
import { useTerminalReconciliation } from './useTerminalReconciliation';

const ARTIFACT_TOOLS = new Set([
  'create_artifact',
  'update_artifact',
  'rewrite_artifact',
]);

// Module-level AbortController shared across all useSSE() instances.
// This ensures that PermissionModal.connect() and useChat.disconnect()
// operate on the same controller, preventing orphaned SSE connections.
let _sharedAbortController: AbortController | null = null;

// Permission decisions arrive before their TOOL_START. Keep them by the same
// native call_id used for every other tool lifecycle join instead of relying on
// event adjacency. The map survives a reconnect within one stream and is reset
// when a new logical stream starts.
const _pendingPermissionResults = new Map<
  string,
  { approved: boolean; reason?: string }
>();

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BASE_DELAY_MS = 1000;
const INJECT_EVENT_PREFIX =
  '[The user has injected a message during execution. Consider this input and adjust your approach as needed.]\n';

function stripInjectEventPrefix(content: string): string {
  return content.startsWith(INJECT_EVENT_PREFIX)
    ? content.slice(INJECT_EVENT_PREFIX.length)
    : content;
}

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
  const confirmPendingInject = useStreamStore((s) => s.confirmPendingInject);
  const setExecutionMetrics = useStreamStore((s) => s.setExecutionMetrics);
  const setCancelled = useStreamStore((s) => s.setCancelled);
  const setReconnecting = useStreamStore((s) => s.setReconnecting);
  const setQueuedInfo = useStreamStore((s) => s.setQueuedInfo);

  // Conversation store actions
  // Artifact store
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const applyArtifactCreated = useArtifactStore((s) => s.applyArtifactCreated);
  const applyArtifactUpdated = useArtifactStore((s) => s.applyArtifactUpdated);

  // UI store
  const autoOpenArtifactPanel = useUIStore((s) => s.autoOpenArtifactPanel);

  const { snapshotTerminalMessage, refreshAfterComplete } =
    useTerminalReconciliation();

  const handleEvent = useCallback(
    (event: SSEEvent, conversationId: string) => {
      const { type, data } = event;

      // TWIN PATH — keep the per-event field mappings below in sync with
      // reconstructSegments.ts, which folds the SAME persisted events into the
      // SAME ExecutionSegment / ToolCallInfo / NonAgentBlock shapes on history
      // reload. Only the field mapping is duplicated; the orchestration genuinely
      // differs (streaming + llm_chunk here vs batch fold there) so the two loops
      // stay separate by design. Add/remove a field on any UI object below →
      // mirror it there (and vice-versa). The `reason` field silently dropping on
      // history reload was exactly this twin drifting out of sync.
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
          // No chunk from the completed invocation may cross this structural
          // boundary. The scheduler is also segment-id-bound, so this cancel
          // protects the old segment's authoritative LLM_COMPLETE snapshot.
          cancelPendingFlush();
          // Mark previous segment as complete — a new turn implies the prior is done
          updateCurrentSegment({ status: 'complete', llmStreamChannel: null });
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
            updateCurrentSegment({ reasoningContent: reasoning, llmStreamChannel: 'reasoning' });
          }

          const rawToolProgress = data?.tool_call_progress;
          if (Array.isArray(rawToolProgress)) {
            updateCurrentSegment({
              toolCallProgress: (rawToolProgress as ToolCallProgressData[]).map((progress) => ({
                index: progress.index,
                ...(progress.call_id ? { callId: progress.call_id } : {}),
                ...(progress.name ? { toolName: progress.name } : {}),
                argumentsChars: progress.arguments_chars,
                status: 'generating' as const,
              })),
              // Native-call output follows reasoning semantically, just like
              // ordinary content, so the thinking indicator is no longer live.
              llmStreamChannel: null,
            });
          }

          const content = data?.content as string | undefined;
          if (content !== undefined) {
            // Auto-fold thinking when content starts arriving
            const currentSeg = useStreamStore.getState().segments;
            const last = currentSeg[currentSeg.length - 1];
            if (last) updateCurrentSegment({ llmStreamChannel: 'content' });
            // Use an id-targeted RAF update so a late frame cannot write into
            // the next LLM invocation's segment.
            if (last) scheduleContentUpdate(last.id, content);
          }
          break;
        }

        case StreamEventType.LLM_COMPLETE: {
          const d = (data ?? {}) as Partial<LLMCompleteData>;
          // This is the authoritative snapshot for the invocation. Drop any
          // RAF-buffered chunk before committing it; native tool_calls remain
          // structured in this event and execution cards come from TOOL_START.
          cancelPendingFlush();

          updateCurrentSegment({
            ...(d.content !== undefined ? { content: d.content } : {}),
            llmStreamChannel: null,
            // The accepted envelope is authoritative.  Keep calls visible as
            // queued until their serial TOOL_START replaces each draft card.
            toolCallProgress: (d.tool_calls ?? []).map((call, index) => ({
              index,
              callId: call.id,
              toolName: call.function.name,
              argumentsChars: call.function.arguments.length,
              status: 'queued' as const,
            })),
            // Backfill reasoning when the provider only delivers it on the
            // final event (no llm_chunk reasoning_content stream). Without
            // this, live shows blank reasoning while replay can — same gap
            // during cancellation cleanup.
            ...(d.reasoning_content ? { reasoningContent: d.reasoning_content } : {}),
            ...(d.token_usage ? { tokenUsage: d.token_usage } : {}),
            ...(d.model ? { model: d.model } : {}),
            ...(d.duration_ms != null ? { llmDurationMs: d.duration_ms } : {}),
          });
          break;
        }

        case StreamEventType.AGENT_COMPLETE:
          updateCurrentSegment({ status: 'complete', llmStreamChannel: null });
          break;

        case StreamEventType.TOOL_START: {
          // TWIN: ToolCallInfo is also built in reconstructSegments.ts `tool_start`
          // (history reload) — keep this field set identical to that one.
          const callId = data?.call_id as string | undefined;
          const toolName = data?.tool as string ?? '';
          const params = data?.params as Record<string, unknown> ?? {};
          const reason = data?.reason as string | undefined;
          const agent = event.agent ?? '';

          if (!callId) {
            console.error('[useSSE] tool_start missing native call_id');
            break;
          }

          const permission = _pendingPermissionResults.get(callId);
          _pendingPermissionResults.delete(callId);

          addToolCallToSegment({
            id: callId,
            toolName,
            params,
            agent,
            status: 'running',
            ...(reason ? { reason } : {}),
            ...(permission ? { permission } : {}),
          });
          break;
        }

        case StreamEventType.TOOL_COMPLETE: {
          const callId = data?.call_id as string | undefined;
          const toolName = data?.tool as string ?? '';
          const success = data?.success as boolean ?? true;
          const result = typeof data?.result_data === 'string'
            ? data.result_data as string
            : !success && typeof data?.error === 'string'
              ? data.error as string
              : JSON.stringify(data?.result_data ?? data?.result ?? '');
          const durationMs = data?.duration_ms as number | undefined;

          // Native call_id is the structural join key. Tool names are not
          // identities: the same tool may appear more than once in one response.
          const segments = useStreamStore.getState().segments;
          const running = callId
            ? segments.some((seg) => seg.toolCalls.some(
              (tc) => tc.id === callId && tc.status === 'running'
            ))
            : false;
          if (!callId || !running) {
            console.error(
              `[useSSE] tool_complete for "${toolName}" has no matching running call_id — engine pairing contract violated`
            );
          } else {
            updateToolCallInSegment(callId, {
              status: success ? 'success' : 'error',
              result,
              durationMs,
            });
          }

          // NOTE: artifact panel open / list upsert / live content are now driven
          // entirely by ARTIFACT_CREATED / ARTIFACT_UPDATED events (see those
          // cases below), NOT by tool_complete + a REST re-fetch. The old path
          // relied on REST overlaying the in-memory cache via
          // ArtifactManager.get_active(), which was removed in the artifact-layer
          // refactor (process-local registry → silently broken across workers).
          // Just surface the panel when an artifact tool ran; the event carries
          // the content. (Tool-persisted outputs emit ARTIFACT_CREATED too.)
          if (success && ARTIFACT_TOOLS.has(toolName)) {
            setArtifactSessionId(conversationId);
            autoOpenArtifactPanel();
          }
          break;
        }

        case StreamEventType.ARTIFACT_CREATED: {
          // Live source of truth during a turn (REST GET is pure-DB now and lags).
          // Reducer upserts the list, auto-opens (every source incl. tool, unless
          // the user actively picked another artifact), and stores live content.
          // DB re-pull on COMPLETE realigns.
          setArtifactSessionId(conversationId);
          autoOpenArtifactPanel();
          applyArtifactCreated(data as unknown as ArtifactCreatedData);
          break;
        }

        case StreamEventType.ARTIFACT_UPDATED: {
          setArtifactSessionId(conversationId);
          autoOpenArtifactPanel();
          applyArtifactUpdated(data as unknown as ArtifactUpdatedData);
          break;
        }

        case StreamEventType.PERMISSION_REQUEST: {
          const callId = data?.call_id as string | undefined;
          if (!callId) {
            console.error('[useSSE] permission_request missing native call_id');
            break;
          }
          setPermissionRequest({
            callId,
            toolName: data?.tool as string ?? '',
            params: data?.params as Record<string, unknown> ?? {},
            reason: data?.reason as string | undefined,
          });
          break;
        }

        case StreamEventType.PERMISSION_RESULT: {
          setPermissionRequest(null);
          const callId = data?.call_id as string | undefined;
          if (!callId) {
            console.error('[useSSE] permission_result missing native call_id');
            break;
          }
          const approved = (data?.approved as boolean) ?? false;
          const reason = data?.reason as string | undefined;
          _pendingPermissionResults.set(
            callId,
            reason ? { approved, reason } : { approved },
          );
          break;
        }

        case StreamEventType.QUEUED_MESSAGE: {
          const content = data?.content as string ?? '';
          // Best-effort only: confirm the local pending inject that matches
          // this engine echo, so replay/cross-tab queued messages don't consume
          // an unrelated waiting pill.
          confirmPendingInject(stripInjectEventPrefix(content));
          pushNonAgentBlock({
            kind: 'inject',
            id: `inject-${Date.now()}`,
            content,
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          break;
        }

        case StreamEventType.COMPACTION_START: {
          const d = data as import('@/types/events').CompactionStartData | undefined;
          pushNonAgentBlock({
            kind: 'compaction',
            id: `compact-${event.timestamp}`,
            state: 'running',
            triggerTokens: d?.last_input_tokens != null && d.last_output_tokens != null
              ? { input: d.last_input_tokens, output: d.last_output_tokens }
              : undefined,
            reason: d?.reason,
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
          snapshotTerminalMessage(conversationId, messageId, data?.response as string | undefined, metrics);
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
          snapshotTerminalMessage(conversationId, messageId, data?.response as string | undefined, metrics);
          const userId = useAuthStore.getState().user?.id;
          if (userId && messageId) notifyTaskTerminal(userId, messageId, 'complete');
          endStream();
          refreshAfterComplete(conversationId, messageId);
          break;
        }

        case StreamEventType.TIMED_OUT: {
          // 执行超时(后端 engine_task 的 asyncio.timeout → TIMED_OUT 终态)。
          // 终态外观复用 COMPLETE 的收尾(snapshot + endStream + refresh):气泡
          // 先用 SSE 的 response 乐观落地,再由刷新后的 Message.response 对齐。
          const metrics = data?.execution_metrics;
          if (metrics) setExecutionMetrics(metrics as import('@/types/events').ExecutionMetrics);
          const messageId = useStreamStore.getState().messageId;
          if (messageId) {
            snapshotSegments(messageId);
          }
          snapshotTerminalMessage(conversationId, messageId, data?.response as string | undefined, metrics);
          const userId = useAuthStore.getState().user?.id;
          if (userId && messageId) notifyTaskTerminal(userId, messageId, 'timed_out');
          endStream();
          refreshAfterComplete(conversationId, messageId);
          break;
        }

        case StreamEventType.ERROR: {
          const errMsg = (data?.error as string) ?? 'Unknown error';
          const reqId = (data?.request_id as string | undefined) || undefined;
          const metrics = data?.execution_metrics;
          if (metrics) setExecutionMetrics(metrics as import('@/types/events').ExecutionMetrics);
          // Push as a flow block FIRST so snapshotSegments captures it into
          // completedNonAgentBlocks. Without this, AssistantMessage's
          // lazy-load gate (completedSegs !== undefined → skip refetch)
          // hides the just-finished failure as a green "Completed" until
          // the page is reloaded — the live/replay regression P1.
          pushNonAgentBlock({
            kind: 'error',
            id: `error-${event.timestamp}`,
            error: errMsg,
            requestId: reqId,
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          // 标准 error 块由 interleave 渲染(带 requestId);standalone 字符串
          // 兜底路径(StreamingMessage 无结构化 requestId)把错误码缀入文本保可见。
          setError(reqId ? `${errMsg}（错误码 ${reqId}）` : errMsg);
          const errMsgId = useStreamStore.getState().messageId;
          if (errMsgId) {
            snapshotSegments(errMsgId);
          }
          snapshotTerminalMessage(conversationId, errMsgId, (data?.response as string | undefined) ?? errMsg, metrics);
          const userId = useAuthStore.getState().user?.id;
          if (userId && errMsgId) notifyTaskTerminal(userId, errMsgId, 'error');
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
      setError, endStream, refreshAfterComplete, autoOpenArtifactPanel,
      setArtifactSessionId,
      applyArtifactCreated, applyArtifactUpdated,
      pushNonAgentBlock, updateNonAgentBlock, confirmPendingInject, setExecutionMetrics, setCancelled,
      setQueuedInfo, snapshotTerminalMessage,
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
          if (!active.active) continue;

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
                if (t === 'complete' || t === 'cancelled' || t === 'timed_out' || t === 'error') {
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
          // getActiveStream failed (network/server error) — try next attempt
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
      // A provider call_id is unique within one turn, not across streams.
      _pendingPermissionResults.clear();

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
            if (t === 'complete' || t === 'cancelled' || t === 'timed_out' || t === 'error') {
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
        if (!active.active) return;
        // The probe is async and switchConversation can fire several in
        // quick succession (e.g. B → C). A late-resolving probe for B must
        // not steal the SSE connection from the now-active C.
        const current = useConversationStore.getState().current;
        if (current?.id !== conversationId) return;
        // Reconnect attaches to a message that getConversation() has already
        // loaded into the persisted branchPath. Leave the parent marker in the
        // "normal persisted path" state; otherwise MessageList would trim the
        // active user message out and, with no local pending bubble, hide it.
        useStreamStore.getState().setStreamParentId(undefined);
        connect(active.stream_url, conversationId, active.message_id);
      } catch {
        // Network/server error — nothing live to attach to for now.
      }
    },
    [connect]
  );

  return { connect, disconnect, reconnectIfActive };
}
