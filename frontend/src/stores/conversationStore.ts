import { create } from 'zustand';
import type {
  ConversationSummary,
  ConversationDetail,
  MessageResponse,
  MessageFeedbackResponse,
  ActivatedSkillRef,
  ReferencedArtifactRef,
} from '@/types';
import { MessageNode, buildMessageTree, extractBranchPath } from '@/lib/messageTree';

// Sidebar active-dot semantics — kept intentionally lightweight:
// `conversation.active_message_id` is a best-effort hint, not a strong-
// consistency state machine. The server list is authoritative on refresh
// (setConversations replaces unconditionally); send + terminal events apply
// lightweight optimistic updates (setConversationActiveMessage / CAS clear).
// We do NOT version list snapshots — under cross-tab divergence, switching
// away mid-run, or rare out-of-order list responses, the dot may be briefly
// stale. Strong "is this conversation running?" semantics belong to the
// streaming view (Stop/Inject UI keyed off streamStore), not this indicator.

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
  updateMessageFeedback: (messageId: string, feedback: MessageFeedbackResponse | null) => void;
  applyTerminalMessageSnapshot: (snapshot: {
    conversationId: string;
    messageId: string;
    parentId: string | null;
    userInput: string;
    response: string;
    executionMetrics?: Record<string, unknown> | null;
    uploadedFiles?: { filename: string }[] | null;
    activatedSkills?: ActivatedSkillRef[] | null;
    referencedArtifacts?: ReferencedArtifactRef[] | null;
  }) => void;
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

  updateMessageFeedback: (messageId, feedback) => {
    const state = get();
    if (!state.current) return;
    const messages = state.current.messages.map((message) =>
      message.id === messageId ? { ...message, feedback } : message
    );
    const nodeMap = buildMessageTree(messages);
    set({
      current: { ...state.current, messages },
      nodeMap,
      branchPath: extractBranchPath(nodeMap, state.activeBranch),
    });
  },

  applyTerminalMessageSnapshot: ({
    conversationId,
    messageId,
    parentId,
    userInput,
    response,
    executionMetrics,
    uploadedFiles,
    activatedSkills,
    referencedArtifacts,
  }) => {
    const now = new Date().toISOString();
    const state = get();
    const current = state.current;
    const existing = current?.id === conversationId ? current : null;
    const messages = existing ? [...existing.messages] : [];
    const idx = messages.findIndex((m) => m.id === messageId);
    const uploaded = uploadedFiles?.map((f) => ({ id: f.filename, filename: f.filename }));

    if (idx >= 0) {
      messages[idx] = {
        ...messages[idx],
        response,
        execution_metrics: executionMetrics ?? messages[idx].execution_metrics ?? null,
        uploaded_files: uploaded ?? messages[idx].uploaded_files ?? null,
        activated_skills: activatedSkills ?? messages[idx].activated_skills ?? null,
        referenced_artifacts:
          referencedArtifacts ?? messages[idx].referenced_artifacts ?? null,
      };
    } else {
      messages.push({
        id: messageId,
        parent_id: parentId,
        user_input: userInput,
        response,
        // The terminal event has no authoritative Message.created_at. Keep the
        // timestamp absent until the REST detail refresh replaces this optimistic
        // node; using `now` here would mislabel completion time as send time.
        created_at: null,
        children: [],
        execution_metrics: executionMetrics ?? null,
        uploaded_files: uploaded ?? null,
        activated_skills: activatedSkills ?? null,
        referenced_artifacts: referencedArtifacts ?? null,
        active_skills: null,
      });
    }

    const title = existing?.title || userInput.trim().split('\n')[0].trim() || 'Untitled';
    get().setCurrent({
      id: conversationId,
      title,
      active_branch: messageId,
      messages,
      session_id: conversationId,
      created_at: existing?.created_at ?? now,
      updated_at: now,
    });
  },

  removeConversation: (id) =>
    set((s) => ({
      conversations: s.conversations.filter((c) => c.id !== id),
      total: s.total - 1,
      current: s.current?.id === id ? null : s.current,
    })),

  setConversationActiveMessage: (id, messageId) =>
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === id);
      if (idx === -1) return s;
      if (s.conversations[idx].active_message_id === messageId) return s;
      const next = [...s.conversations];
      next[idx] = { ...next[idx], active_message_id: messageId };
      return { conversations: next };
    }),

  clearConversationActiveIfMatch: (id, terminalMessageId) =>
    set((s) => {
      const idx = s.conversations.findIndex((c) => c.id === id);
      if (idx === -1) return s;
      // Compare-and-clear: only clear when the cached id matches the terminal.
      // If a new turn has already optimistically replaced active_message_id,
      // the old terminal is a no-op.
      if (s.conversations[idx].active_message_id !== terminalMessageId) return s;
      const next = [...s.conversations];
      next[idx] = { ...next[idx], active_message_id: null };
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
