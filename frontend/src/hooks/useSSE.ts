'use client';

import { useCallback, useRef } from 'react';
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

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null);

  // Stream store actions
  const pushSegment = useStreamStore((s) => s.pushSegment);
  const updateCurrentSegment = useStreamStore((s) => s.updateCurrentSegment);
  const addToolCallToSegment = useStreamStore((s) => s.addToolCallToSegment);
  const updateToolCallInSegment = useStreamStore((s) => s.updateToolCallInSegment);
  const snapshotSegments = useStreamStore((s) => s.snapshotSegments);
  const setPermissionRequest = useStreamStore((s) => s.setPermissionRequest);
  const setError = useStreamStore((s) => s.setError);
  const endStream = useStreamStore((s) => s.endStream);

  // Conversation store actions
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);

  // Artifact store
  const setArtifactSessionId = useArtifactStore((s) => s.setSessionId);
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const setArtifactCurrent = useArtifactStore((s) => s.setCurrent);
  const setArtifactVersions = useArtifactStore((s) => s.setVersions);
  const addPendingUpdate = useArtifactStore((s) => s.addPendingUpdate);
  const clearPendingUpdates = useArtifactStore((s) => s.clearPendingUpdates);

  // UI store
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const refreshAfterComplete = useCallback(
    async (conversationId: string) => {
      try {
        const [detail, list] = await Promise.all([
          api.getConversation(conversationId),
          api.listConversations(20, 0),
        ]);
        setCurrent(detail);
        setConversations(list.conversations, list.total, list.has_more);

        if (detail.session_id) {
          try {
            const artifacts = await api.listArtifacts(detail.session_id);
            setArtifacts(artifacts.artifacts);
            clearPendingUpdates();
          } catch {
            // Artifacts may not exist yet
          }
        }
      } catch (err) {
        console.error('Failed to refresh after complete:', err);
      }
    },
    [setCurrent, setConversations, setArtifacts, clearPendingUpdates]
  );

  const handleEvent = useCallback(
    (event: SSEEvent, conversationId: string) => {
      const { type, data } = event;

      switch (type) {
        case StreamEventType.METADATA: {
          // Dev-only consistency check: verify IDs from metadata match streamStore
          if (process.env.NODE_ENV === 'development') {
            const metaThreadId = data?.thread_id as string | undefined;
            const metaMsgId = data?.message_id as string | undefined;
            const store = useStreamStore.getState();
            if (metaThreadId && store.threadId && metaThreadId !== store.threadId) {
              console.warn('[SSE] thread_id mismatch:', { meta: metaThreadId, store: store.threadId });
            }
            if (metaMsgId && store.messageId && metaMsgId !== store.messageId) {
              console.warn('[SSE] message_id mismatch:', { meta: metaMsgId, store: store.messageId });
            }
          }
          break;
        }

        case StreamEventType.AGENT_START: {
          const agentName = data?.agent_name as string ?? event.agent ?? 'Agent';
          pushSegment(agentName);
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

          updateCurrentSegment({
            ...(finalContent ? { content: finalContent } : {}),
            isThinking: false,
            ...llmOutputUpdate,
          });
          break;
        }

        case StreamEventType.AGENT_COMPLETE:
          updateCurrentSegment({ status: 'complete' });
          break;

        case StreamEventType.TOOL_START: {
          const toolName = data?.tool_name as string ?? event.tool ?? '';
          const params = data?.params as Record<string, unknown> ?? {};
          const agent = data?.agent as string ?? event.agent ?? '';

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
          const toolName = data?.tool_name as string ?? event.tool ?? '';
          const success = data?.success as boolean ?? true;
          const result = typeof data?.result_data === 'string'
            ? data.result_data as string
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
              Promise.all([
                api.getArtifact(sessionId, artifactId),
                api.listArtifacts(sessionId),
                api.listVersions(sessionId, artifactId),
              ]).then(([detail, list, versions]) => {
                setArtifactCurrent(detail);
                setArtifacts(list.artifacts);
                setArtifactVersions(versions.versions);
              }).catch(() => {
                // Artifact may not be persisted yet; will retry on stream complete
              });
            }
          }
          break;
        }

        case StreamEventType.PERMISSION_REQUEST:
          setPermissionRequest({
            toolName: data?.tool_name as string ?? event.tool ?? '',
            params: data?.params as Record<string, unknown> ?? {},
          });
          break;

        case StreamEventType.PERMISSION_RESULT:
          setPermissionRequest(null);
          break;

        case StreamEventType.COMPLETE: {
          const interrupted = data?.interrupted as boolean | undefined;
          if (interrupted) {
            // Permission interrupt: preserve full stream state (isStreaming,
            // segments, threadId, messageId, permissionRequest) so the UI
            // stays in streaming mode and PermissionModal can function.
            // SSE connection closes naturally; resumeStream reconnects later.
            break;
          }
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
      setArtifactVersions,
    ]
  );

  const connect = useCallback(
    (streamUrl: string, conversationId: string, _messageId: string) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }

      const controller = new AbortController();
      abortRef.current = controller;

      connectSSE(
        streamUrl,
        {
          onEvent: (event) => handleEvent(event, conversationId),
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
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    endStream();
  }, [endStream]);

  return { connect, disconnect };
}
