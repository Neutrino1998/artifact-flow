import { create } from 'zustand';

function applyTheme(theme: 'light' | 'dark') {
  if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }
}

export type UserMgmtRightView =
  | { type: 'empty' }
  | { type: 'create-user' }
  | { type: 'edit-user'; userId: string }
  | { type: 'dept-manager' }
  | { type: 'bulk-import' }
  | { type: 'bulk-action' };

interface UIState {
  sidebarCollapsed: boolean;
  artifactPanelVisible: boolean;
  // Monotonic counter bumped on every write that affects what occupies
  // the right panel: artifact toggle / explicit set, user-management
  // open/close (master-detail), observability open/close (full-screen
  // takeover). Lets deferred callers (e.g. useChat's auto-open-on-switch)
  // snapshot the value before an await and detect ANY user-driven right-
  // panel intent change in between — a plain boolean snapshot of
  // `artifactPanelVisible` cannot distinguish "untouched" from
  // "toggled and toggled back", and ignores siblings (user-mgmt /
  // observability) that also re-target the right panel.
  rightPanelIntentEpoch: number;
  conversationBrowserVisible: boolean;
  userManagementVisible: boolean;
  userManagementRightView: UserMgmtRightView;
  // 列表刷新版本号 — 右面板表单（创建/编辑/删除）成功后 bump，
  // UserManagementPanel 订阅版本号触发 refetch，避免 prop 钻透
  userMgmtListVersion: number;
  // PR5a: 中间面板的选择模式 + 选中集；与 RightView 协调（进入选择模式
  // 自动切到 'bulk-action'，退出回 'empty'）
  selectionMode: boolean;
  userManagementSelection: string[];
  observabilityVisible: boolean;
  observabilitySelectedConvId: string | null;
  observabilityBrowseVisible: boolean;
  observabilityRefreshTick: number;
  theme: 'light' | 'dark';

  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleArtifactPanel: () => void;
  setArtifactPanelVisible: (visible: boolean) => void;
  setConversationBrowserVisible: (visible: boolean) => void;
  setUserManagementVisible: (visible: boolean) => void;
  setUserManagementRightView: (view: UserMgmtRightView) => void;
  bumpUserMgmtListVersion: () => void;
  enterSelectionMode: () => void;
  exitSelectionMode: () => void;
  toggleUserSelection: (userId: string) => void;
  setUserManagementSelection: (ids: string[]) => void;
  clearUserSelection: () => void;
  setObservabilityVisible: (visible: boolean) => void;
  setObservabilitySelectedConvId: (id: string | null) => void;
  setObservabilityBrowseVisible: (visible: boolean) => void;
  triggerObservabilityRefresh: () => void;
  setTheme: (theme: 'light' | 'dark') => void;
  toggleTheme: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  artifactPanelVisible: false,
  rightPanelIntentEpoch: 0,
  conversationBrowserVisible: false,
  userManagementVisible: false,
  userManagementRightView: { type: 'empty' },
  userMgmtListVersion: 0,
  selectionMode: false,
  userManagementSelection: [],
  observabilityVisible: false,
  observabilitySelectedConvId: null,
  observabilityBrowseVisible: false,
  observabilityRefreshTick: 0,
  theme: 'dark',

  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  toggleArtifactPanel: () =>
    set((s) => ({
      artifactPanelVisible: !s.artifactPanelVisible,
      rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    })),
  setArtifactPanelVisible: (visible) =>
    set((s) => ({
      artifactPanelVisible: visible,
      rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    })),
  setConversationBrowserVisible: (visible) => set({
    conversationBrowserVisible: visible,
    ...(visible && {
      userManagementVisible: false,
      userManagementRightView: { type: 'empty' },
      selectionMode: false,
      userManagementSelection: [],
      observabilityVisible: false,
    }),
  }),
  setUserManagementVisible: (visible) => set((s) => ({
    userManagementVisible: visible,
    // Bump on both open and close: opening re-targets right panel to
    // UserManagementDetailPanel; closing releases it back to ArtifactPanel.
    // Either edge is a user-driven right-panel intent change.
    rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    ...(visible && { conversationBrowserVisible: false, observabilityVisible: false }),
    ...(!visible && {
      userManagementRightView: { type: 'empty' },
      selectionMode: false,
      userManagementSelection: [],
    }),
  })),
  setUserManagementRightView: (view) => set({ userManagementRightView: view }),
  bumpUserMgmtListVersion: () =>
    set((s) => ({ userMgmtListVersion: s.userMgmtListVersion + 1 })),
  enterSelectionMode: () => set({
    selectionMode: true,
    userManagementSelection: [],
    userManagementRightView: { type: 'bulk-action' },
  }),
  exitSelectionMode: () => set({
    selectionMode: false,
    userManagementSelection: [],
    userManagementRightView: { type: 'empty' },
  }),
  toggleUserSelection: (userId) => set((s) => {
    const has = s.userManagementSelection.includes(userId);
    return {
      userManagementSelection: has
        ? s.userManagementSelection.filter((id) => id !== userId)
        : [...s.userManagementSelection, userId],
    };
  }),
  setUserManagementSelection: (ids) => set({ userManagementSelection: ids }),
  clearUserSelection: () => set({ userManagementSelection: [] }),
  setObservabilityVisible: (visible) => set((s) => ({
    observabilityVisible: visible,
    // Bump on both open and close: opening hides the right panel entirely
    // (full-screen takeover); closing releases it back. Either edge is a
    // user-driven right-panel intent change.
    rightPanelIntentEpoch: s.rightPanelIntentEpoch + 1,
    ...(visible && {
      conversationBrowserVisible: false,
      userManagementVisible: false,
      userManagementRightView: { type: 'empty' },
      selectionMode: false,
      userManagementSelection: [],
      artifactPanelVisible: false,
    }),
    ...(!visible && { observabilitySelectedConvId: null, observabilityBrowseVisible: false }),
  })),
  setObservabilitySelectedConvId: (id) => set({
    observabilitySelectedConvId: id,
    observabilityBrowseVisible: false,
  }),
  setObservabilityBrowseVisible: (visible) => set({
    observabilityBrowseVisible: visible,
  }),
  triggerObservabilityRefresh: () => set((s) => ({
    observabilityRefreshTick: s.observabilityRefreshTick + 1,
  })),

  setTheme: (theme) => {
    applyTheme(theme);
    set({ theme });
  },
  toggleTheme: () =>
    set((s) => {
      const next = s.theme === 'light' ? 'dark' : 'light';
      localStorage.setItem('theme', next);
      applyTheme(next);
      return { theme: next };
    }),
}));
