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
  | { type: 'create-dept'; parentId: string | null }
  | { type: 'edit-dept'; deptId: string }
  | { type: 'bulk-action' };

interface UIState {
  sidebarCollapsed: boolean;
  artifactPanelVisible: boolean;
  conversationBrowserVisible: boolean;
  userManagementVisible: boolean;
  userManagementRightView: UserMgmtRightView;
  // 列表刷新版本号 — 右面板表单（创建/编辑/删除）成功后 bump，
  // UserManagementPanel 订阅版本号触发 refetch，避免 prop 钻透
  userMgmtListVersion: number;
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
  conversationBrowserVisible: false,
  userManagementVisible: false,
  userManagementRightView: { type: 'empty' },
  userMgmtListVersion: 0,
  observabilityVisible: false,
  observabilitySelectedConvId: null,
  observabilityBrowseVisible: false,
  observabilityRefreshTick: 0,
  theme: 'dark',

  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  toggleArtifactPanel: () =>
    set((s) => ({ artifactPanelVisible: !s.artifactPanelVisible })),
  setArtifactPanelVisible: (visible) => set({ artifactPanelVisible: visible }),
  setConversationBrowserVisible: (visible) => set({
    conversationBrowserVisible: visible,
    ...(visible && {
      userManagementVisible: false,
      userManagementRightView: { type: 'empty' },
      observabilityVisible: false,
    }),
  }),
  setUserManagementVisible: (visible) => set({
    userManagementVisible: visible,
    ...(visible && { conversationBrowserVisible: false, observabilityVisible: false }),
    ...(!visible && { userManagementRightView: { type: 'empty' } }),
  }),
  setUserManagementRightView: (view) => set({ userManagementRightView: view }),
  bumpUserMgmtListVersion: () =>
    set((s) => ({ userMgmtListVersion: s.userMgmtListVersion + 1 })),
  setObservabilityVisible: (visible) => set({
    observabilityVisible: visible,
    ...(visible && {
      conversationBrowserVisible: false,
      userManagementVisible: false,
      userManagementRightView: { type: 'empty' },
      artifactPanelVisible: false,
    }),
    ...(!visible && { observabilitySelectedConvId: null, observabilityBrowseVisible: false }),
  }),
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
