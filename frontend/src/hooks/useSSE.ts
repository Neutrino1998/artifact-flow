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
  const setCurrentAgent = useStreamStore((s) => s.setCurrentAgent);
  const setLastEventType = useStreamStore((s) => s.setLastEventType);
  const addToolCall = useStreamStore((s) => s.addToolCall);
  const updateToolCall = useStreamStore((s) => s.updateToolCall);
  const setPermissionRequest = useStreamStore((s) => s.setPermissionRequest);
  const setError = useStreamStore((s) => s.setError);
  const endStream = useStreamStore((s) => s.endStream);
  const setReasoningContent = useStreamStore((s) => s.setReasoningContent);
  const setIsThinking = useStreamStore((s) => s.setIsThinking);
  const setStreamContent = useStreamStore((s) => s.setStreamContent);

  // Conversation store actions
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const setConversations = useConversationStore((s) => s.setConversations);

  // Artifact store
  const setArtifacts = useArtifactStore((s) => s.setArtifacts);
  const addPendingUpdate = useArtifactStore((s) => s.addPendingUpdate);
  const clearPendingUpdates = useArtifactStore((s) => s.clearPendingUpdates);

  // UI store
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const refreshAfterComplete = useCallback(
    async (conversationId: string) => {
      try {
        // Refresh conversation detail + list in parallel
        const [detail, list] = await Promise.all([
          api.getConversation(conversationId),
          api.listConversations(20, 0),
        ]);
        setCurrent(detail);
        setConversations(list.conversations, list.total, list.has_more);

        // Refresh artifacts if session exists
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
      setLastEventType(type);

      switch (type) {
        case StreamEventType.METADATA:
          // Metadata already captured in startStream
          break;

        case StreamEventType.AGENT_START:
          setCurrentAgent(data?.agent_name as string ?? event.agent ?? null);
          break;

        case StreamEventType.LLM_CHUNK: {
          // Handle reasoning_content (thinking)
          const reasoning = data?.reasoning_content as string | undefined;
          if (reasoning !== undefined) {
            setReasoningContent(reasoning);
            if (!useStreamStore.getState().isThinking) {
              setIsThinking(true);
            }
          }

          // content is CUMULATIVE from backend
          const content = data?.content as string | undefined;
          if (content !== undefined) {
            // Auto-fold thinking when content starts
            if (useStreamStore.getState().isThinking) {
              setIsThinking(false);
            }
            // Use RAF-throttled update
            scheduleContentUpdate(content);
          }
          break;
        }

        case StreamEventType.LLM_COMPLETE:
          // Final content — set the full content (bypass throttle)
          if (data?.content) {
            setStreamContent(data.content as string);
          }
          setIsThinking(false);
          break;

        case StreamEventType.AGENT_COMPLETE:
          // Agent round done — may loop for more tool calls
          break;

        case StreamEventType.TOOL_START: {
          const toolName = data?.tool_name as string ?? event.tool ?? '';
          const params = data?.params as Record<string, unknown> ?? {};
          const agent = data?.agent as string ?? event.agent ?? '';
          addToolCall({
            id: `${toolName}-${Date.now()}`,
            toolName,
            params,
            agent,
            status: 'running',
          });
          // Clear streaming content when entering tool phase
          setStreamContent('');
          break;
        }

        case StreamEventType.TOOL_COMPLETE: {
          const toolName = data?.tool_name as string ?? event.tool ?? '';
          const success = data?.success as boolean ?? true;
          const result = typeof data?.result_data === 'string'
            ? data.result_data as string
            : JSON.stringify(data?.result_data ?? data?.result ?? '');
          const durationMs = data?.duration_ms as number | undefined;

          // Find the matching running tool call to update
          const toolCalls = useStreamStore.getState().toolCalls;
          const running = toolCalls.find(
            (tc) => tc.toolName === toolName && tc.status === 'running'
          );
          if (running) {
            updateToolCall(running.id, {
              status: success ? 'success' : 'error',
              result,
              durationMs,
            });
          }

          // Auto-open artifact panel on artifact tool completion
          if (ARTIFACT_TOOLS.has(toolName) && success) {
            setArtifactPanelVisible(true);
            // Track pending update for refresh on complete
            const artifactId = data?.artifact_id as string | undefined;
            if (artifactId) {
              addPendingUpdate(artifactId);
            }
          }
          break;
        }

        case StreamEventType.PERMISSION_REQUEST:
          setPermissionRequest({
            toolName: data?.tool_name as string ?? event.tool ?? '',
            params: data?.params as Record<string, unknown> ?? {},
            messageId: data?.message_id as string ?? '',
            threadId: data?.thread_id as string ?? '',
          });
          break;

        case StreamEventType.PERMISSION_RESULT:
          setPermissionRequest(null);
          break;

        case StreamEventType.COMPLETE: {
          endStream();
          // Refresh conversation to get final response from DB
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
      setLastEventType, setCurrentAgent, setStreamContent, setReasoningContent,
      setIsThinking, addToolCall, updateToolCall, setPermissionRequest,
      setError, endStream, refreshAfterComplete, setArtifactPanelVisible,
      addPendingUpdate,
    ]
  );

  const connect = useCallback(
    (streamUrl: string, conversationId: string, _messageId: string) => {
      // Abort any existing connection
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
