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
    set({ activeBranch: messageId, branchPath });
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
