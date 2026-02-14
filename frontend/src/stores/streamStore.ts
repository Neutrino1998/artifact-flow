import { create } from 'zustand';

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
}

export interface ExecutionSegment {
  id: string;                    // `${agent}-${timestamp}`
  agent: string;
  status: 'running' | 'complete';
  reasoningContent: string;
  isThinking: boolean;
  toolCalls: ToolCallInfo[];
  content: string;
  llmOutput: string;             // raw LLM output preserved before content is cleared at tool_start
}

interface StreamState {
  // Connection
  isStreaming: boolean;
  streamUrl: string | null;
  threadId: string | null;
  messageId: string | null;
  conversationId: string | null;

  // Segment-based timeline
  segments: ExecutionSegment[];

  // Pending user message (shown before conversation loads)
  pendingUserMessage: string | null;

  // Parent ID for rerun/edit branching (controls branchPath truncation)
  // undefined = normal send, null = root rerun, string = rerun from specific parent
  streamParentId: string | null | undefined;

  // Completed segments cache (session-only, keyed by messageId)
  completedSegments: Map<string, ExecutionSegment[]>;

  // Permission
  permissionRequest: PermissionRequest | null;

  // Error
  error: string | null;

  // Actions
  startStream: (url: string, threadId: string, messageId: string, conversationId: string) => void;
  resumeStream: (url: string) => void;
  endStream: () => void;
  reset: () => void;

  // Segment actions
  pushSegment: (agent: string) => void;
  updateCurrentSegment: (update: Partial<ExecutionSegment>) => void;
  appendCurrentSegmentContent: (content: string) => void;
  addToolCallToSegment: (tc: ToolCallInfo) => void;
  updateToolCallInSegment: (id: string, update: Partial<ToolCallInfo>) => void;

  // Pending user message
  setPendingUserMessage: (msg: string | null) => void;
  setStreamParentId: (id: string | null | undefined) => void;

  // Snapshot segments for completed messages
  snapshotSegments: (messageId: string) => void;

  // Permission / error
  setPermissionRequest: (req: PermissionRequest | null) => void;
  setError: (error: string | null) => void;
}

// RAF-based throttle for segment content updates
let _rafId: number | null = null;
let _pendingContent: string | null = null;
let _appendFn: ((content: string) => void) | null = null;

function flushContent() {
  if (_pendingContent !== null && _appendFn) {
    _appendFn(_pendingContent);
    _pendingContent = null;
  }
  _rafId = null;
}

export function scheduleContentUpdate(content: string) {
  _pendingContent = content;
  if (_rafId === null && typeof requestAnimationFrame !== 'undefined') {
    _rafId = requestAnimationFrame(flushContent);
  } else if (typeof requestAnimationFrame === 'undefined') {
    flushContent();
  }
}

export const useStreamStore = create<StreamState>((set, get) => {
  // Capture appendCurrentSegmentContent for RAF throttle after store creation
  // We use a wrapper that calls get() to always get the latest action reference
  _appendFn = (content: string) => {
    get().appendCurrentSegmentContent(content);
  };

  return {
    isStreaming: false,
    streamUrl: null,
    threadId: null,
    messageId: null,
    conversationId: null,
    segments: [],
    pendingUserMessage: null,
    streamParentId: undefined,
    completedSegments: new Map(),
    permissionRequest: null,
    error: null,

    startStream: (url, threadId, messageId, conversationId) =>
      set({
        isStreaming: true,
        streamUrl: url,
        threadId,
        messageId,
        conversationId,
        segments: [],
        permissionRequest: null,
        error: null,
      }),

    resumeStream: (url) =>
      set({
        isStreaming: true,
        streamUrl: url,
        permissionRequest: null,
        error: null,
      }),

    endStream: () =>
      set({ isStreaming: false, streamUrl: null, conversationId: null, permissionRequest: null, streamParentId: undefined }),

    reset: () =>
      set({
        isStreaming: false,
        streamUrl: null,
        threadId: null,
        messageId: null,
        conversationId: null,
        segments: [],
        pendingUserMessage: null,
        streamParentId: undefined,
        permissionRequest: null,
        error: null,
      }),

    pushSegment: (agent) =>
      set((s) => ({
        segments: [
          ...s.segments,
          {
            id: `${agent}-${Date.now()}`,
            agent,
            status: 'running',
            reasoningContent: '',
            isThinking: false,
            toolCalls: [],
            content: '',
            llmOutput: '',
          },
        ],
      })),

    updateCurrentSegment: (update) =>
      set((s) => {
        const segs = s.segments;
        if (segs.length === 0) return s;
        const last = segs[segs.length - 1];
        return {
          segments: [...segs.slice(0, -1), { ...last, ...update }],
        };
      }),

    appendCurrentSegmentContent: (content) =>
      set((s) => {
        const segs = s.segments;
        if (segs.length === 0) return s;
        const last = segs[segs.length - 1];
        return {
          segments: [...segs.slice(0, -1), { ...last, content }],
        };
      }),

    addToolCallToSegment: (tc) =>
      set((s) => {
        const segs = s.segments;
        if (segs.length === 0) return s;
        const last = segs[segs.length - 1];
        return {
          segments: [
            ...segs.slice(0, -1),
            { ...last, toolCalls: [...last.toolCalls, tc] },
          ],
        };
      }),

    updateToolCallInSegment: (id, update) =>
      set((s) => {
        const segs = s.segments;
        if (segs.length === 0) return s;
        // Search all segments for the tool call (it may be in a previous segment)
        const newSegs = segs.map((seg) => {
          const idx = seg.toolCalls.findIndex((tc) => tc.id === id);
          if (idx === -1) return seg;
          const newToolCalls = [...seg.toolCalls];
          newToolCalls[idx] = { ...newToolCalls[idx], ...update };
          return { ...seg, toolCalls: newToolCalls };
        });
        return { segments: newSegs };
      }),

    setPendingUserMessage: (msg) => set({ pendingUserMessage: msg }),
    setStreamParentId: (id) => set({ streamParentId: id }),

    snapshotSegments: (messageId) => {
      const state = get();
      // Only snapshot if there are intermediate segments (more than just the final one with content)
      const segsToSnapshot = state.segments.filter(
        (seg) => seg.toolCalls.length > 0 || seg.reasoningContent
      );
      if (segsToSnapshot.length > 0) {
        const newMap = new Map(state.completedSegments);
        // Deep copy to prevent stale references
        newMap.set(messageId, JSON.parse(JSON.stringify(segsToSnapshot)));
        set({ completedSegments: newMap });
      }
    },

    setPermissionRequest: (req) => set({ permissionRequest: req }),
    setError: (error) => set({ error }),
  };
});

// Convenience selectors
export const selectCurrentSegment = (s: StreamState) =>
  s.segments[s.segments.length - 1] ?? null;

export const selectStreamContent = (s: StreamState) =>
  s.segments[s.segments.length - 1]?.content ?? '';
