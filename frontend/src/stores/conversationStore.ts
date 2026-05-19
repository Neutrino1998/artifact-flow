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

  // Current conversation
  current: ConversationDetail | null;
  currentLoading: boolean;

  // Message tree
  nodeMap: Map<string, MessageNode>;
  branchPath: MessageNode[];
  activeBranch: string | null;

  // Actions
  setConversations: (convs: ConversationSummary[], total: number, hasMore: boolean) => void;
  appendConversations: (convs: ConversationSummary[], total: number, hasMore: boolean) => void;
  setListLoading: (loading: boolean) => void;
  setCurrent: (conv: ConversationDetail | null) => void;
  setCurrentLoading: (loading: boolean) => void;
  setActiveBranch: (messageId: string | null) => void;
  updateMessages: (messages: MessageResponse[]) => void;
  removeConversation: (id: string) => void;
  /** Flip is_active on a single cached conv. Used to drive the sidebar
   *  running-indicator without waiting for the next list refresh. */
  markConversationActive: (id: string, active: boolean) => void;
  reset: () => void;
}

export const useConversationStore = create<ConversationState>((set, get) => ({
  conversations: [],
  total: 0,
  hasMore: false,
  listLoading: false,

  current: null,
  currentLoading: false,

  nodeMap: new Map(),
  branchPath: [],
  activeBranch: null,

  setConversations: (convs, total, hasMore) =>
    set({ conversations: convs, total, hasMore }),

  appendConversations: (convs, total, hasMore) =>
    set((s) => ({
      conversations: [...s.conversations, ...convs],
      total,
      hasMore,
    })),

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
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      total: s.total - 1,
      current: s.current?.id === id ? null : s.current,
    })),

  markConversationActive: (id, active) =>
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === id);
      if (idx === -1) return s;
      if (s.conversations[idx].is_active === active) return s;
      const next = [...s.conversations];
      next[idx] = { ...next[idx], is_active: active };
      return { conversations: next };
    }),

  reset: () =>
    set({
      conversations: [],
      total: 0,
      hasMore: false,
      current: null,
      nodeMap: new Map(),
      branchPath: [],
      activeBranch: null,
    }),
}));
