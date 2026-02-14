'use client';

import { useUIStore } from '@/stores/uiStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifactStore } from '@/stores/artifactStore';
import ConversationList from './ConversationList';

function IconButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="w-10 h-10 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
      aria-label={label}
      title={label}
    >
      {children}
    </button>
  );
}

export default function Sidebar() {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const toggleTheme = useUIStore((s) => s.toggleTheme);
  const theme = useUIStore((s) => s.theme);
  const toggleArtifactPanel = useUIStore((s) => s.toggleArtifactPanel);
  const setCurrent = useConversationStore((s) => s.setCurrent);
  const reset = useStreamStore((s) => s.reset);
  const resetArtifacts = useArtifactStore((s) => s.reset);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const handleNewChat = () => {
    setCurrent(null);
    reset();
    resetArtifacts();
    setArtifactPanelVisible(false);
  };

  // ── Collapsed: 48px icon bar ──
  if (sidebarCollapsed) {
    return (
      <div className="flex flex-col items-center h-full bg-surface dark:bg-surface-dark py-3 gap-1 w-12">
        {/* New chat */}
        <IconButton onClick={handleNewChat} label="新建对话">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M8 3v10M3 8h10" />
          </svg>
        </IconButton>

        {/* Artifacts */}
        <IconButton onClick={toggleArtifactPanel} label="文稿面板">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="2" y="2" width="12" height="12" rx="1.5" />
            <path d="M5 6h6M5 8.5h4" />
          </svg>
        </IconButton>

        {/* Theme toggle */}
        <IconButton onClick={toggleTheme} label={theme === 'light' ? '深色模式' : '浅色模式'}>
          {theme === 'light' ? (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M8 1v1m0 12v1m7-7h-1M2 8H1m12.07-4.07-.71.71M3.64 12.36l-.71.71m10.14 0-.71-.71M3.64 3.64l-.71-.71M11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />
            </svg>
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M14 9.27A6 6 0 0 1 6.73 2 6 6 0 1 0 14 9.27z" />
            </svg>
          )}
        </IconButton>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Expand */}
        <IconButton onClick={toggleSidebar} label="展开侧栏">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M6 3l5 5-5 5" />
          </svg>
        </IconButton>
      </div>
    );
  }

  // ── Expanded: full sidebar ──
  return (
    <div className="flex flex-col h-full bg-surface dark:bg-surface-dark">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border dark:border-border-dark">
        <h1 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark">
          ArtifactFlow
        </h1>
        <div className="flex items-center gap-1">
          <button
            onClick={toggleArtifactPanel}
            className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Toggle artifact panel"
            title="文稿面板"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="12" height="12" rx="1.5" />
              <path d="M5 6h6M5 8.5h4" />
            </svg>
          </button>
          <button
            onClick={toggleTheme}
            className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Toggle theme"
            title={theme === 'light' ? '切换深色模式' : '切换浅色模式'}
          >
            {theme === 'light' ? (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M8 1v1m0 12v1m7-7h-1M2 8H1m12.07-4.07-.71.71M3.64 12.36l-.71.71m10.14 0-.71-.71M3.64 3.64l-.71-.71M11 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M14 9.27A6 6 0 0 1 6.73 2 6 6 0 1 0 14 9.27z" />
              </svg>
            )}
          </button>
          <button
            onClick={toggleSidebar}
            className="p-1.5 rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Collapse sidebar"
            title="收起侧栏"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M2 4h12M2 8h12M2 12h12" />
            </svg>
          </button>
        </div>
      </div>

      {/* New chat button */}
      <div className="px-3 pt-3 pb-3">
        <button
          onClick={handleNewChat}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-white bg-accent hover:bg-accent-hover rounded-card transition-colors"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M7 2v10M2 7h10" />
          </svg>
          新建对话
        </button>
      </div>

      {/* Conversation list */}
      <ConversationList />
    </div>
  );
}
