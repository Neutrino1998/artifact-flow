import { create } from 'zustand';
import type { StreamEventType } from '@/types/events';

export interface ToolCallInfo {
  id: string;
  toolName: string;
  params: Record<string, unknown>;
  agent: string;
  status: 'running' | 'success' | 'error';
  result?: string;
  durationMs?: number;
}

export interface PermissionRequest {
  toolName: string;
  params: Record<string, unknown>;
  messageId: string;
  threadId: string;
}

interface StreamState {
  // Connection
  isStreaming: boolean;
  streamUrl: string | null;
  threadId: string | null;
  messageId: string | null;

  // Content
  streamContent: string;
  currentAgent: string | null;
  lastEventType: StreamEventType | string | null;

  // Reasoning / thinking
  reasoningContent: string;
  isThinking: boolean;

  // Tool calls (append-only during stream)
  toolCalls: ToolCallInfo[];

  // Permission
  permissionRequest: PermissionRequest | null;

  // Error
  error: string | null;

  // Actions
  startStream: (url: string, threadId: string, messageId: string) => void;
  appendChunk: (chunk: string) => void;
  setStreamContent: (content: string) => void;
  setCurrentAgent: (agent: string | null) => void;
  setLastEventType: (type: StreamEventType | string) => void;
  setReasoningContent: (content: string) => void;
  setIsThinking: (thinking: boolean) => void;
  addToolCall: (tc: ToolCallInfo) => void;
  updateToolCall: (id: string, update: Partial<ToolCallInfo>) => void;
  setPermissionRequest: (req: PermissionRequest | null) => void;
  setError: (error: string | null) => void;
  endStream: () => void;
  reset: () => void;
}

// RAF-based throttle for stream content updates
let _rafId: number | null = null;
let _pendingContent: string | null = null;
let _setContentFn: ((content: string) => void) | null = null;

function flushContent() {
  if (_pendingContent !== null && _setContentFn) {
    _setContentFn(_pendingContent);
    _pendingContent = null;
  }
  _rafId = null;
}

export function scheduleContentUpdate(content: string) {
  _pendingContent = content;
  if (_rafId === null && typeof requestAnimationFrame !== 'undefined') {
    _rafId = requestAnimationFrame(flushContent);
  } else if (typeof requestAnimationFrame === 'undefined') {
    // SSR fallback
    flushContent();
  }
}

export const useStreamStore = create<StreamState>((set) => {
  // Capture the raw setter for RAF throttle
  const rawSetContent = (content: string) => set({ streamContent: content });
  _setContentFn = rawSetContent;

  return {
    isStreaming: false,
    streamUrl: null,
    threadId: null,
    messageId: null,

    streamContent: '',
    currentAgent: null,
    lastEventType: null,

    reasoningContent: '',
    isThinking: false,

    toolCalls: [],

    permissionRequest: null,

    error: null,

    startStream: (url, threadId, messageId) =>
      set({
        isStreaming: true,
        streamUrl: url,
        threadId,
        messageId,
        streamContent: '',
        currentAgent: null,
        lastEventType: null,
        reasoningContent: '',
        isThinking: false,
        toolCalls: [],
        permissionRequest: null,
        error: null,
      }),

    appendChunk: (chunk) =>
      set((s) => ({ streamContent: s.streamContent + chunk })),

    setStreamContent: rawSetContent,

    setCurrentAgent: (agent) => set({ currentAgent: agent }),

    setLastEventType: (type) => set({ lastEventType: type }),

    setReasoningContent: (content) => set({ reasoningContent: content }),

    setIsThinking: (thinking) => set({ isThinking: thinking }),

    addToolCall: (tc) =>
      set((s) => ({ toolCalls: [...s.toolCalls, tc] })),

    updateToolCall: (id, update) =>
      set((s) => ({
        toolCalls: s.toolCalls.map((tc) =>
          tc.id === id ? { ...tc, ...update } : tc
        ),
      })),

    setPermissionRequest: (req) => set({ permissionRequest: req }),

    setError: (error) => set({ error }),

    endStream: () =>
      set({ isStreaming: false, streamUrl: null, permissionRequest: null }),

    reset: () =>
      set({
        isStreaming: false,
        streamUrl: null,
        threadId: null,
        messageId: null,
        streamContent: '',
        currentAgent: null,
        lastEventType: null,
        reasoningContent: '',
        isThinking: false,
        toolCalls: [],
        permissionRequest: null,
        error: null,
      }),
  };
});
