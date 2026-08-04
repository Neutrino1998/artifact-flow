import { create } from 'zustand';
import type { ExecutionMetrics, TokenUsage } from '@/types/events';
import type { ActivatedSkillRef } from '@/types';

export interface ToolCallInfo {
  /** Provider-issued native tool-call id; joins llm_complete/start/complete. */
  id: string;
  toolName: string;
  params: Record<string, unknown>;
  agent: string;
  status: 'running' | 'success' | 'error';
  result?: string;
  durationMs?: number;
  /** Optional backend-supplied display explanation; not model reasoning.
   *  Distinct from `permission.reason` below, which is the decision outcome. */
  reason?: string;
  /** Set only for CONFIRM-level tools — the user's response (or timeout).
   *  Engine emits permission_request → permission_result immediately before
   *  the tool's own tool_start, so live + replay both pair by time. */
  permission?: { approved: boolean; reason?: string };
}

export interface PermissionRequest {
  toolName: string;
  params: Record<string, unknown>;
  /** Optional backend-supplied display explanation; not model reasoning. */
  reason?: string;
}

/** User message injected during agent execution (QUEUED_MESSAGE event). */
export interface InjectBlock {
  kind: 'inject';
  id: string;
  content: string;
  timestamp: string;
  position: number;   // segments.length at insertion time
}

/** Local-only mirror for an inject POST that has not appeared as QUEUED_MESSAGE yet. */
export interface PendingInjectBlock {
  kind: 'pending_inject';
  id: string;
  content: string;
  timestamp: string;
  position: number;
}

/** Context-compaction block. Lifecycle: running → done | error. */
export interface CompactionBlock {
  kind: 'compaction';
  id: string;
  state: 'running' | 'done' | 'error';
  /** input+output tokens of the LLM call that tripped the threshold (from COMPACTION_START) */
  triggerTokens?: { input: number; output: number };
  /** compacted summary text (frame-prepended; from COMPACTION_SUMMARY) */
  summary?: string;
  /** compact_agent's model id — shown in header next to usage, mirroring agent segment */
  model?: string;
  /** compact_agent's own LLM cost */
  tokenUsage?: TokenUsage;
  /** compact_agent LLM duration in ms */
  durationMs?: number;
  /** populated when compact LLM failed — in that case `summary` is a placeholder */
  error?: string | null;
  timestamp: string;
  position: number;
}

/** Error event surfaced inline in the flow timeline (replay-only — live path
 *  uses the standalone streamStore.error / ErrorFlowBlock). */
export interface ErrorBlock {
  kind: 'error';
  id: string;
  error: string;
  /** 可回传的请求级错误码（req-xxxx），运维凭它 grep 完整堆栈。
   *  实时路径由 SSE error 事件 data.request_id 填充；replay 重建可能缺省。 */
  requestId?: string;
  timestamp: string;
  position: number;
}

export type NonAgentBlock = InjectBlock | CompactionBlock | ErrorBlock;
export type TimelineBlock = NonAgentBlock | PendingInjectBlock;

export type FlowItem =
  | { kind: 'agent'; segment: ExecutionSegment; index: number }
  | TimelineBlock;

/** Interleave agent segments with non-agent blocks by insertion position. */
export function interleaveFlowItems(
  segments: ExecutionSegment[],
  blocks: TimelineBlock[],
): FlowItem[] {
  const sorted = [...blocks].sort((a, b) => a.position - b.position);
  const result: FlowItem[] = [];
  let bIdx = 0;
  for (let i = 0; i < segments.length; i++) {
    while (bIdx < sorted.length && sorted[bIdx].position <= i) {
      result.push(sorted[bIdx]);
      bIdx++;
    }
    result.push({ kind: 'agent', segment: segments[i], index: i });
  }
  while (bIdx < sorted.length) {
    result.push(sorted[bIdx]);
    bIdx++;
  }
  return result;
}

export interface ExecutionSegment {
  id: string;                    // `${agent}-${timestamp}`
  agent: string;
  status: 'running' | 'complete';
  reasoningContent: string;
  isThinking: boolean;
  toolCalls: ToolCallInfo[];
  content: string;
  tokenUsage?: TokenUsage;
  model?: string;
  llmDurationMs?: number;
}

interface StreamState {
  // Connection
  isStreaming: boolean;
  streamUrl: string | null;
  messageId: string | null;
  conversationId: string | null;

  // Segment-based timeline
  segments: ExecutionSegment[];

  // Pending user message (shown before conversation loads)
  pendingUserMessage: string | null;

  // Filenames attached to the pending user message — optimistic mirror of the
  // persisted MessageResponse.uploaded_files, so the live bubble shows the
  // attachments without waiting for the turn to flush. Same lifecycle as
  // pendingUserMessage (set on send, cleared on reset).
  pendingUserFiles: string[] | null;

  // Skills explicitly selected for the pending user message. This is a
  // per-turn display snapshot; it must not be confused with cumulative
  // active_skills or model-initiated read_skill activation.
  pendingUserSkills: ActivatedSkillRef[] | null;

  // Parent ID for rerun/edit branching (controls branchPath truncation)
  // undefined = normal send, null = root rerun, string = rerun from specific parent
  streamParentId: string | null | undefined;

  // Completed segments cache (session-only, keyed by messageId)
  completedSegments: Map<string, ExecutionSegment[]>;

  // Non-agent blocks (inject / compaction) interleaved with segments
  nonAgentBlocks: NonAgentBlock[];

  // Local-only injects accepted by the composer but not yet echoed by the
  // engine as queued_message. Cleared by FIFO when the real event arrives.
  pendingInjects: PendingInjectBlock[];

  // Completed non-agent blocks cache (session-only, keyed by messageId)
  completedNonAgentBlocks: Map<string, NonAgentBlock[]>;

  // Execution metrics summary (from COMPLETE event)
  executionMetrics: ExecutionMetrics | null;

  // Permission
  permissionRequest: PermissionRequest | null;

  // Cancelled
  cancelled: boolean;

  // Cancel requested but engine hasn't reached a checkpoint yet
  // (cancel signal queues into the engine — see CANCEL_CHECK_INTERVAL).
  // Drives the "cancelling…" spinner on the Stop button.
  cancelling: boolean;

  // Reconnecting (SSE auto-reconnect in progress)
  reconnecting: boolean;

  // Error — surfaced AFTER a stream connected (SSE ERROR event / transport
  // failure). Rendered inside the message flow (StreamingMessage live,
  // AssistantMessage once persisted), so it must NOT be re-surfaced globally.
  error: string | null;

  // Pre-stream send failure — the POST /api/v1/chat failed before connect()
  // ever flipped isStreaming, so no stream/message exists to host it. Distinct
  // from `error` precisely so the composer banner (ChatPanel) can show it
  // without double-rendering a terminal `error` that already lives in the flow.
  sendError: string | null;

  // Concurrency queue indicator (SSE-only, cleared on first agent_start / end / reset)
  queuedInfo: { ahead: number; maxConcurrent: number } | null;

  // Actions
  startStream: (url: string, messageId: string, conversationId: string) => void;
  endStream: () => void;
  reset: () => void;

  // Segment actions
  pushSegment: (agent: string) => void;
  updateCurrentSegment: (update: Partial<ExecutionSegment>) => void;
  updateSegmentContent: (segmentId: string, content: string) => void;
  addToolCallToSegment: (tc: ToolCallInfo) => void;
  updateToolCallInSegment: (id: string, update: Partial<ToolCallInfo>) => void;

  // Pending user message
  setPendingUserMessage: (msg: string | null) => void;
  setPendingUserFiles: (files: string[] | null) => void;
  setPendingUserSkills: (skills: ActivatedSkillRef[] | null) => void;
  setStreamParentId: (id: string | null | undefined) => void;

  // Non-agent blocks / metrics
  pushNonAgentBlock: (block: NonAgentBlock) => void;
  /** Merge a patch into an existing NonAgentBlock by id. Used to transition a
      CompactionBlock from running→done/error when COMPACTION_SUMMARY arrives. */
  updateNonAgentBlock: (id: string, patch: Partial<CompactionBlock>) => void;
  addPendingInject: (content: string) => string;
  removePendingInject: (id: string) => void;
  confirmPendingInject: (content: string) => void;
  setExecutionMetrics: (metrics: ExecutionMetrics) => void;

  // Snapshot segments for completed messages
  snapshotSegments: (messageId: string) => void;

  // Reconnecting
  setReconnecting: (val: boolean) => void;

  // Cancelled / Permission / error
  setCancelled: (val: boolean) => void;
  setCancelling: (val: boolean) => void;
  setPermissionRequest: (req: PermissionRequest | null) => void;
  setError: (error: string | null) => void;
  setSendError: (sendError: string | null) => void;

  // Queue indicator
  setQueuedInfo: (info: { ahead: number; maxConcurrent: number } | null) => void;
}

// RAF-based throttle for segment content updates
let _rafId: number | null = null;
let _pendingContent: { segmentId: string; content: string } | null = null;
let _appendFn: ((segmentId: string, content: string) => void) | null = null;
let _pendingInjectSeq = 0;

function flushContent() {
  if (_pendingContent !== null && _appendFn) {
    _appendFn(_pendingContent.segmentId, _pendingContent.content);
    _pendingContent = null;
  }
  _rafId = null;
}

/** Cancel any pending RAF flush and clear buffered content. */
export function cancelPendingFlush() {
  if (_rafId !== null && typeof cancelAnimationFrame !== 'undefined') {
    cancelAnimationFrame(_rafId);
  }
  _rafId = null;
  _pendingContent = null;
}

export function scheduleContentUpdate(segmentId: string, content: string) {
  _pendingContent = { segmentId, content };
  if (_rafId === null && typeof requestAnimationFrame !== 'undefined') {
    _rafId = requestAnimationFrame(flushContent);
  } else if (typeof requestAnimationFrame === 'undefined') {
    flushContent();
  }
}

export const useStreamStore = create<StreamState>((set, get) => {
  // Capture the id-targeted content action for RAF throttling. Binding a
  // snapshot to its segment makes a late frame unable to mutate a newer LLM
  // invocation after AGENT_START has advanced the timeline.
  // We use a wrapper that calls get() to always get the latest action reference
  _appendFn = (segmentId: string, content: string) => {
    get().updateSegmentContent(segmentId, content);
  };

  return {
    isStreaming: false,
    streamUrl: null,
    messageId: null,
    conversationId: null,
    segments: [],
    pendingUserMessage: null,
    pendingUserFiles: null,
    pendingUserSkills: null,
    streamParentId: undefined,
    completedSegments: new Map(),
    nonAgentBlocks: [],
    pendingInjects: [],
    completedNonAgentBlocks: new Map(),
    executionMetrics: null,
    reconnecting: false,
    cancelled: false,
    cancelling: false,
    permissionRequest: null,
    error: null,
    sendError: null,
    queuedInfo: null,

    startStream: (url, messageId, conversationId) => {
      cancelPendingFlush();
      set({
        isStreaming: true,
        streamUrl: url,
        messageId,
        conversationId,
        segments: [],
        nonAgentBlocks: [],
        pendingInjects: [],
        executionMetrics: null,
        reconnecting: false,
        cancelled: false,
        cancelling: false,
        permissionRequest: null,
        error: null,
        sendError: null,
        queuedInfo: null,
      });
    },

    endStream: () => {
      cancelPendingFlush();
      set({ isStreaming: false, streamUrl: null, conversationId: null, reconnecting: false, cancelled: false, cancelling: false, permissionRequest: null, streamParentId: undefined, queuedInfo: null, pendingInjects: [] });
    },

    reset: () =>
      set({
        isStreaming: false,
        streamUrl: null,
        messageId: null,
        conversationId: null,
        segments: [],
        pendingUserMessage: null,
        pendingUserFiles: null,
        pendingUserSkills: null,
        pendingInjects: [],
        streamParentId: undefined,
        permissionRequest: null,
        error: null,
        sendError: null,
        queuedInfo: null,
      }),

    pushSegment: (agent) =>
      set((s) => ({
        segments: [
          ...s.segments,
          // Insertion index suffix is the collision-resistance bit: SSE replay
          // can deliver multiple agent_start events through handleEvent in the
          // same JS tick, so Date.now() alone would collide and trip React's
          // duplicate-key warning.
          {
            id: `${agent}-${Date.now()}-${s.segments.length}`,
            agent,
            status: 'running',
            reasoningContent: '',
            isThinking: false,
            toolCalls: [],
            content: '',
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

    updateSegmentContent: (segmentId, content) =>
      set((s) => {
        const segs = s.segments;
        const idx = segs.findIndex((seg) => seg.id === segmentId);
        if (idx === -1) return s;
        const newSegs = [...segs];
        newSegs[idx] = { ...newSegs[idx], content };
        return { segments: newSegs };
      }),

    addToolCallToSegment: (tc) =>
      set((s) => {
        const segs = s.segments;
        if (segs.length === 0) return s;
        // Lane by agent: with in-place subagent recursion (nested serial
        // delegation) the caller's later tool_starts arrive AFTER the
        // subagent's segment, so "append to last" would misfile them onto
        // the subagent's lane. Fall back to the last segment when no lane
        // matches (agent missing on old replays).
        let idx = segs.length - 1;
        for (let i = segs.length - 1; i >= 0; i--) {
          if (segs[i].agent === tc.agent) {
            idx = i;
            break;
          }
        }
        const target = segs[idx];
        const newSegs = [...segs];
        newSegs[idx] = { ...target, toolCalls: [...target.toolCalls, tc] };
        return { segments: newSegs };
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
    setPendingUserFiles: (files) => set({ pendingUserFiles: files }),
    setPendingUserSkills: (skills) => set({ pendingUserSkills: skills }),
    setStreamParentId: (id) => set({ streamParentId: id }),

    pushNonAgentBlock: (block) =>
      set((s) => ({ nonAgentBlocks: [...s.nonAgentBlocks, block] })),
    updateNonAgentBlock: (id, patch) =>
      set((s) => ({
        nonAgentBlocks: s.nonAgentBlocks.map((b) =>
          b.id === id && b.kind === 'compaction'
            ? ({ ...b, ...patch } as CompactionBlock)
            : b
        ),
      })),
    addPendingInject: (content) => {
      _pendingInjectSeq += 1;
      const id = `pending-inject-${Date.now()}-${_pendingInjectSeq}`;
      set((s) => ({
        pendingInjects: [
          ...s.pendingInjects,
          {
            kind: 'pending_inject',
            id,
            content,
            timestamp: new Date().toISOString(),
            position: s.segments.length,
          },
        ],
      }));
      return id;
    },
    removePendingInject: (id) =>
      set((s) => ({
        pendingInjects: s.pendingInjects.filter((p) => p.id !== id),
      })),
    confirmPendingInject: (content) =>
      set((s) => {
        const idx = s.pendingInjects.findIndex((p) => p.content === content);
        if (idx === -1) return {};
        return {
          pendingInjects: [
            ...s.pendingInjects.slice(0, idx),
            ...s.pendingInjects.slice(idx + 1),
          ],
        };
      }),
    setExecutionMetrics: (metrics) => set({ executionMetrics: metrics }),

    snapshotSegments: (messageId) => {
      const state = get();
      // Only snapshot if there are intermediate segments (more than just the final one with content)
      const segsToSnapshot = state.segments
        .filter((seg) => seg.toolCalls.length > 0 || seg.reasoningContent)
        // Execution is done — mark any remaining 'running' segments as 'complete'
        .map((seg) => seg.status === 'running' ? { ...seg, status: 'complete' as const } : seg);
      if (segsToSnapshot.length > 0) {
        const newMap = new Map(state.completedSegments);
        // Deep copy to prevent stale references
        newMap.set(messageId, JSON.parse(JSON.stringify(segsToSnapshot)));
        set({ completedSegments: newMap });
      }
      // Snapshot non-agent blocks. Compaction is now persistent (COMPACTION_SUMMARY
      // is a DB event), so both inject and compaction blocks are retained in cache.
      const blocksToSnapshot = state.nonAgentBlocks;
      if (blocksToSnapshot.length > 0) {
        const nabMap = new Map(state.completedNonAgentBlocks);
        nabMap.set(messageId, blocksToSnapshot);
        set({ completedNonAgentBlocks: nabMap });
      }
    },

    setReconnecting: (val) => set({ reconnecting: val }),
    setCancelled: (val) => set({ cancelled: val }),
    setCancelling: (val) => set({ cancelling: val }),
    setPermissionRequest: (req) => set({ permissionRequest: req }),
    setError: (error) => set({ error }),
    setSendError: (sendError) => set({ sendError }),
    setQueuedInfo: (info) => set({ queuedInfo: info }),
  };
});

// Convenience selectors
export const selectCurrentSegment = (s: StreamState) =>
  s.segments[s.segments.length - 1] ?? null;

export const selectStreamContent = (s: StreamState) =>
  s.segments[s.segments.length - 1]?.content ?? '';
