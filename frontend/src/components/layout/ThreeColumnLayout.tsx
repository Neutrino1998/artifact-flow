'use client';

import { useCallback, useRef, useEffect, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useMediaQuery, BREAKPOINTS } from '@/hooks/useMediaQuery';

const MIN_CHAT_WIDTH = 400;
const MIN_ARTIFACT_WIDTH = 300;
const DEFAULT_ARTIFACT_WIDTH = 480;

interface ThreeColumnLayoutProps {
  sidebar:
    | React.ReactNode
    | ((props: {
        variant: 'desktop' | 'drawer';
        onNavigate: () => void;
      }) => React.ReactNode);
  chat: React.ReactNode;
  artifact?: React.ReactNode;
  rightPanelLabel?: string;
  // 3-state visibility override for the right panel:
  //   true       → force show (e.g. desktop master-detail mode)
  //   false      → force hide (e.g. mobile fallback that must not be auto-shown)
  //   undefined  → defer to user-controlled artifactPanelVisible
  forceArtifactVisible?: boolean;
}

export default function ThreeColumnLayout({
  sidebar,
  chat,
  artifact,
  rightPanelLabel = '文件面板',
  forceArtifactVisible,
}: ThreeColumnLayoutProps) {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const artifactPanelVisible = useUIStore((s) => s.artifactPanelVisible);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const isLg = useMediaQuery(BREAKPOINTS.lg);
  const isMd = useMediaQuery(BREAKPOINTS.md);

  // Auto-collapse sidebar based on breakpoints
  useEffect(() => {
    if (!isLg) {
      setSidebarCollapsed(true);
    }
  }, [isLg, setSidebarCollapsed]);

  // The drawer is a transient mobile surface, not another representation of
  // the desktop collapsed rail. Close it when leaving the mobile breakpoint
  // and support the same Escape exit as other temporary overlays.
  useEffect(() => {
    if (isMd) setMobileMenuOpen(false);
  }, [isMd]);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileMenuOpen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [mobileMenuOpen]);

  // Draggable divider state
  const [artifactWidth, setArtifactWidth] = useState(DEFAULT_ARTIFACT_WIDTH);
  const isDragging = useRef(false);
  // Mirrors isDragging for rendering the drag overlay (the ref alone can't drive a
  // re-render). Flipped only on drag start/end — never in mousemove — so it adds no
  // per-move cost. The overlay sits above the artifact panel so the cursor lands on
  // a same-document div, not an embedded iframe (e.g. the HTML artifact preview).
  // Without it, an iframe swallows the document-level mousemove/mouseup and the drag
  // sticks / never releases.
  const [dragging, setDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    setDragging(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current || !containerRef.current) return;
      const containerRect = containerRef.current.getBoundingClientRect();
      const newArtifactWidth = containerRect.right - e.clientX;
      // Card widths include the p-2 gutter: collapsed w-16=64px, expanded w-[17rem]=272px.
      const sidebarWidth = sidebarCollapsed ? (isMd ? 64 : 0) : 272;
      const maxArtifactWidth = containerRect.width - sidebarWidth - MIN_CHAT_WIDTH;
      const clamped = Math.max(MIN_ARTIFACT_WIDTH, Math.min(maxArtifactWidth, newArtifactWidth));
      setArtifactWidth(clamped);
    };

    const handleMouseUp = () => {
      if (isDragging.current) {
        isDragging.current = false;
        setDragging(false);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [sidebarCollapsed, isMd]);

  const handleDoubleClick = useCallback(() => {
    setArtifactWidth(DEFAULT_ARTIFACT_WIDTH);
  }, []);

  const showArtifact = (forceArtifactVisible ?? artifactPanelVisible) && artifact;
  const showSidebar = isMd; // < 768px: sidebar completely hidden
  const closeMobileMenu = useCallback(() => setMobileMenuOpen(false), []);
  const renderSidebar = (
    variant: 'desktop' | 'drawer',
    onNavigate: () => void,
  ) => typeof sidebar === 'function' ? sidebar({ variant, onNavigate }) : sidebar;

  return (
    <div
      ref={containerRef}
      className="flex h-screen [height:100dvh] overflow-hidden bg-chat dark:bg-chat-dark"
    >
      {/* Drag overlay — present only while resizing. Covers the whole layout
          (including any artifact-preview iframe) so the cursor stays on a
          same-document surface and mousemove/mouseup keep reaching `document`. */}
      {dragging && <div className="fixed inset-0 z-50 cursor-col-resize" />}

      {/* Mobile menu button — visible below md */}
      {!isMd && !mobileMenuOpen && !showArtifact && (
        <button
          onClick={() => setMobileMenuOpen(true)}
          className="fixed top-[calc(env(safe-area-inset-top)+0.75rem)] left-[calc(env(safe-area-inset-left)+0.75rem)] z-50 h-11 w-11 flex items-center justify-center rounded-card bg-panel-accent dark:bg-panel-dark border border-border dark:border-border-dark shadow-sidebar-card text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
          aria-label="展开侧栏"
          title="展开侧栏"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="1.5" y="1.5" width="13" height="13" rx="2" />
            <path d="M6 1.5v13" />
          </svg>
        </button>
      )}

      {/* Mobile sidebar overlay */}
      {!isMd && mobileMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/40"
            onClick={closeMobileMenu}
          />
          <aside
            aria-label="主菜单"
            className="fixed inset-y-0 left-0 z-40 w-[min(17rem,calc(100vw-2rem))] p-2 pt-[calc(env(safe-area-inset-top)+0.5rem)] pb-[calc(env(safe-area-inset-bottom)+0.5rem)] pl-[calc(env(safe-area-inset-left)+0.5rem)]"
          >
            <div className="h-full w-full rounded-card overflow-hidden bg-panel-accent dark:bg-panel-dark border border-border dark:border-border-dark shadow-sidebar-card">
              {renderSidebar('drawer', closeMobileMenu)}
            </div>
          </aside>
        </>
      )}

      {/* Sidebar — floating rounded card; hidden below md, icon-bar when collapsed.
          The p-2 gutter is the gap that separates the card from the backdrop and
          the chat column, replacing the old border-r divider. Wrapper widths
          include the 16px gutter (card = wrapper − p-2): w-14→48px, w-[17rem]→256px. */}
      {showSidebar && (
        <div
          className={`flex-shrink-0 transition-[width] duration-150 ease-out relative z-10 p-2 ${
            sidebarCollapsed ? 'w-16' : 'w-[17rem]'
          }`}
        >
          <div className="h-full w-full rounded-card overflow-hidden bg-panel-accent dark:bg-panel-dark border border-border dark:border-border-dark shadow-sidebar-card">
            {renderSidebar('desktop', () => {})}
          </div>
        </div>
      )}

      {/* Chat — takes remaining space */}
      <div className="flex-1 min-w-0 flex flex-col pt-[calc(env(safe-area-inset-top)+3.75rem)] md:pt-0">
        {chat}
      </div>

      {/* Artifact panel */}
      {showArtifact && (
        <>
          {/* On mobile: overlay mode */}
          {!isMd ? (
            <>
              <div
                className="fixed inset-0 z-30 bg-black/40"
                onClick={() => setArtifactPanelVisible(false)}
              />
              <div className="fixed inset-y-0 right-0 z-30 w-[85vw] max-w-lg bg-chat dark:bg-chat-dark border-l border-border dark:border-border-dark overflow-hidden pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] flex flex-col">
                <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-border dark:border-border-dark bg-chat dark:bg-chat-dark">
                  <span className="font-medium text-text-secondary dark:text-text-secondary-dark">
                    {rightPanelLabel}
                  </span>
                  <button
                    onClick={() => setArtifactPanelVisible(false)}
                    className="h-11 w-11 flex items-center justify-center rounded-lg text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-surface-dark"
                    aria-label={`关闭${rightPanelLabel}`}
                    title={`关闭${rightPanelLabel}`}
                  >
                    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                      <path d="M4 4l10 10M14 4L4 14" />
                    </svg>
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">{artifact}</div>
              </div>
            </>
          ) : (
            <>
              {/* Drag handle */}
              <div
                onMouseDown={handleMouseDown}
                onDoubleClick={handleDoubleClick}
                className="w-1 flex-shrink-0 bg-border dark:bg-border-dark hover:bg-accent cursor-col-resize transition-colors"
              />
              <div
                className="flex-shrink-0 overflow-hidden"
                style={{ width: artifactWidth }}
              >
                {artifact}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
