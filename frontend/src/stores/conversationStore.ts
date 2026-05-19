import { create } from 'zustand';
import type {
  ConversationSummary,
  ConversationDetail,
  MessageResponse,
} from '@/types';
import { MessageNode, buildMessageTree, extractBranchPath } from '@/lib/messageTree';

interface ConversationState {
  // List
  conversations: ConversationSummary[];
  total: number;
  hasMore: boolean;
  listLoading: boolean;
  /** Per-conv "last local mutation" wall-clock timestamp. Bumped by
   *  setConversationActiveMessage / clearConversationActiveIfMatch.
   *  setConversations / appendConversations compare each incoming snapshot's
   *  `snapshotTakenAt` against this map; if the snapshot is older, the
   *  conv's `active_message_id` is preserved from cache (other fields take
   *  the server value). Defeats the race where a list GET sent BEFORE a
   *  local optimistic write returns AFTER it and silently wipes the mark.
   *  Single-threaded JS + monotonic Date.now() on one tab → no clock drift
   *  to worry about (cross-tab races are settled by the next list refresh
   *  reading the authoritative lease store). */
  localMutationTimes: Map<string, number>;

  // Current conversation
  current: ConversationDetail | null;
  currentLoading: boolean;

  // Message tree
  nodeMap: Map<string, MessageNode>;
  branchPath: MessageNode[];
  activeBranch: string | null;

  // Actions
  setConversations: (
    convs: ConversationSummary[],
    total: number,
    hasMore: boolean,
    snapshotTakenAt: number,
  ) => void;
  appendConversations: (
    convs: ConversationSummary[],
    total: number,
    hasMore: boolean,
    snapshotTakenAt: number,
  ) => void;
  setListLoading: (loading: boolean) => void;
  setCurrent: (conv: ConversationDetail | null) => void;
  setCurrentLoading: (loading: boolean) => void;
  setActiveBranch: (messageId: string | null) => void;
  updateMessages: (messages: MessageResponse[]) => void;
  removeConversation: (id: string) => void;
  /** Optimistically set the cached active_message_id for a conv. Called by
   *  sendMessage after the server responds with a fresh message_id so the
   *  sidebar dot lights up without waiting for the next list refresh. */
  setConversationActiveMessage: (id: string, messageId: string) => void;
  /** Compare-and-clear the cached active_message_id. Only clears when the
   *  cached id equals terminalMessageId, so an old turn's terminal event
   *  cannot wipe out a newer turn's optimistic mark. */
  clearConversationActiveIfMatch: (id: string, terminalMessageId: string) => void;
  reset: () => void;
}

/** Apply the version-aware merge for a single incoming conv summary.
 *  If `snapshotTakenAt` predates the per-conv local mutation, keep the
 *  cached `active_message_id` (other fields still take the server value). */
function mergeIncomingConv(
  incoming: ConversationSummary,
  cached: ConversationSummary | undefined,
  localMutationAt: number | undefined,
  snapshotTakenAt: number,
): ConversationSummary {
  if (cached && localMutationAt !== undefined && snapshotTakenAt < localMutationAt) {
    return { ...incoming, active_message_id: cached.active_message_id };
  }
  return incoming;
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  conversations: [],
  total: 0,
  hasMore: false,
  listLoading: false,
  localMutationTimes: new Map(),

  current: null,
  currentLoading: false,

  nodeMap: new Map(),
  branchPath: [],
  activeBranch: null,

  setConversations: (convs, total, hasMore, snapshotTakenAt) =>
    set((s) => {
      const cachedById = new Map(s.conversations.map((c) => [c.id, c]));
      const merged = convs.map((c) =>
        mergeIncomingConv(c, cachedById.get(c.id), s.localMutationTimes.get(c.id), snapshotTakenAt)
      );
      return { conversations: merged, total, hasMore };
    }),

  appendConversations: (convs, total, hasMore, snapshotTakenAt) =>
    set((s) => {
      const cachedById = new Map(s.conversations.map((c) => [c.id, c]));
      const merged = convs.map((c) =>
        mergeIncomingConv(c, cachedById.get(c.id), s.localMutationTimes.get(c.id), snapshotTakenAt)
      );
      return { conversations: [...s.conversations, ...merged], total, hasMore };
    }),

  setListLoading: (loading) => set({ listLoading: loading }),

  setCurrent: (conv) => {
    if (!conv) {
      set({ current: null, nodeMap: new Map(), branchPath: [], activeBranch: null });
      return;
    }
    const nodeMap = buildMessageTree(conv.messages);
    const activeBranch = conv.active_branch;
    const branchPath = extractBranchPath(nodeMap, activeBranch);
    set({ current: conv, nodeMap, branchPath, activeBranch });
  },

  setCurrentLoading: (loading) => set({ currentLoading: loading }),

  setActiveBranch: (messageId) => {
    const { nodeMap } = get();
    const branchPath = extractBranchPath(nodeMap, messageId);
    // extractBranchPath resolves through to the deepest leaf — keep activeBranch
    // pointing at that leaf so it stays consistent with branchPath (and with the
    // "active_branch = leaf" contract) when an interior node is selected.
    const activeBranch =
      branchPath.length > 0 ? branchPath[branchPath.length - 1].id : messageId;
    set({ activeBranch, branchPath });
  },

  updateMessages: (messages) => {
    const { activeBranch } = get();
    const nodeMap = buildMessageTree(messages);
    const branchPath = extractBranchPath(nodeMap, activeBranch);
    set((s) => ({
      current: s.current ? { ...s.current, messages } : null,
      nodeMap,
      branchPath,
    }));
  },

  removeConversation: (id) =>
    set((s) => {
      const localMutationTimes = new Map(s.localMutationTimes);
      localMutationTimes.delete(id);
      return {
        conversations: s.conversations.filter((c) => c.id !== id),
        total: s.total - 1,
        current: s.current?.id === id ? null : s.current,
        localMutationTimes,
      };
    }),

  setConversationActiveMessage: (id, messageId) =>
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === id);
      if (idx === -1) return s;
      // Always bump localMutationTimes — even when active_message_id is
      // unchanged, the bump expresses "the current cached value is my
      // intent; in-flight server snapshots taken before now must not
      // overwrite it." Skipping the bump on equality would let a stale
      // snapshot whose server view is null still win.
      const localMutationTimes = new Map(s.localMutationTimes);
      localMutationTimes.set(id, Date.now());
      if (s.conversations[idx].active_message_id === messageId) {
        return { localMutationTimes };
      }
      const next = [...s.conversations];
      next[idx] = { ...next[idx], active_message_id: messageId };
      return { conversations: next, localMutationTimes };
    }),

  clearConversationActiveIfMatch: (id, terminalMessageId) =>
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === id);
      if (idx === -1) return s;
      // Compare-and-clear: only clear when the cached id matches the terminal.
      // If a new turn has already optimistically replaced active_message_id,
      // the old terminal is a no-op (and we don't bump localMutationTimes
      // either — the new turn's bump should win the merge guard).
      if (s.conversations[idx].active_message_id !== terminalMessageId) return s;
      const next = [...s.conversations];
      next[idx] = { ...next[idx], active_message_id: null };
      const localMutationTimes = new Map(s.localMutationTimes);
      localMutationTimes.set(id, Date.now());
      return { conversations: next, localMutationTimes };
    }),

  reset: () =>
    set({
      conversations: [],
      total: 0,
      hasMore: false,
      localMutationTimes: new Map(),
      current: null,
      nodeMap: new Map(),
      branchPath: [],
      activeBranch: null,
    }),
}));
