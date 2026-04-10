import { create } from 'zustand';

function applyTheme(theme: 'light' | 'dark') {
  if (typeof document !== 'undefined') {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }
}

interface UIState {
  sidebarCollapsed: boolean;
  artifactPanelVisible: boolean;
  conversationBrowserVisible: boolean;
  theme: 'light' | 'dark';

  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  toggleArtifactPanel: () => void;
  setArtifactPanelVisible: (visible: boolean) => void;
  setConversationBrowserVisible: (visible: boolean) => void;
  setTheme: (theme: 'light' | 'dark') => void;
  toggleTheme: () => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarCollapsed: false,
  artifactPanelVisible: false,
  conversationBrowserVisible: false,
  theme: 'dark',

  toggleSidebar: () =>
    set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  toggleArtifactPanel: () =>
    set((s) => ({ artifactPanelVisible: !s.artifactPanelVisible })),
  setArtifactPanelVisible: (visible) => set({ artifactPanelVisible: visible }),
  setConversationBrowserVisible: (visible) => set({ conversationBrowserVisible: visible }),

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
