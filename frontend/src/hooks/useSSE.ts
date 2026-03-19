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
  const setExecutionMetrics = useStreamStore((s) => s.setExecutionMetrics);
  const setCancelled = useStreamStore((s) => s.setCancelled);

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
        setCurrent(detail);
        setConversations(list.conversations, list.total, list.has_more);
        clearPendingUpdates();
      } catch (err) {
        console.error('Failed to refresh after complete:', err);
      }
    },
    [setCurrent, setConversations, clearPendingUpdates]
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

          // Find the matching running tool call across all segments
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
          if (runningId) {
            updateToolCallInSegment(runningId, {
              status: success ? 'success' : 'error',
              result,
              durationMs,
            });
          }

          // Auto-open artifact panel and fetch content on artifact tool completion
          if (ARTIFACT_TOOLS.has(toolName) && success) {
            setArtifactPanelVisible(true);
            const params = data?.params as Record<string, unknown> | undefined;
            const artifactId = params?.id as string | undefined;
            if (artifactId) {
              addPendingUpdate(artifactId);
              // session_id == conversation_id in the backend;
              // store it so useArtifacts can use it even before conversation loads
              const sessionId = conversationId;
              setArtifactSessionId(sessionId);
              // Clear selectedVersion so the panel shows the latest current.content
              setSelectedVersion(null);
              Promise.all([
                api.getArtifact(sessionId, artifactId),
                api.listArtifacts(sessionId),
              ]).then(([detail, list]) => {
                setArtifactCurrent(detail);
                setArtifacts(list.artifacts);
                setArtifactVersions(detail.versions);
                setSelectedVersion(detail.latest_version ?? null);
              }).catch(() => {
                // Artifact may not be persisted yet; will retry on stream complete
              });
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

        case StreamEventType.COMPACTION_WAIT:
          pushNonAgentBlock({
            kind: 'compaction',
            id: `compact-${Date.now()}`,
            timestamp: event.timestamp,
            position: useStreamStore.getState().segments.length,
          });
          break;

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

        case StreamEventType.ERROR:
          setError(data?.error as string ?? 'Unknown error');
          endStream();
          break;

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
      pushNonAgentBlock, setExecutionMetrics, setCancelled,
    ]
  );

  const connect = useCallback(
    (streamUrl: string, conversationId: string, _messageId: string) => {
      if (_sharedAbortController) {
        _sharedAbortController.abort();
      }

      const controller = new AbortController();
      _sharedAbortController = controller;

      connectSSE(
        streamUrl,
        {
          onEvent: (event) => {
            if (controller.signal.aborted) return;
            handleEvent(event, conversationId);
          },
          onError: (err) => {
            setError(err.message);
            endStream();
          },
          onClose: () => {
            // Connection closed — stream may have ended naturally
          },
        },
        controller.signal
      );
    },
    [handleEvent, setError, endStream]
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
