'use client';

import { useCallback, useRef, useEffect, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';
import { useMediaQuery, BREAKPOINTS } from '@/hooks/useMediaQuery';

const MIN_CHAT_WIDTH = 400;
const MIN_ARTIFACT_WIDTH = 300;
const DEFAULT_ARTIFACT_WIDTH = 480;

interface ThreeColumnLayoutProps {
  sidebar: React.ReactNode;
  chat: React.ReactNode;
  artifact?: React.ReactNode;
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
  forceArtifactVisible,
}: ThreeColumnLayoutProps) {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore((s) => s.setSidebarCollapsed);
  const artifactPanelVisible = useUIStore((s) => s.artifactPanelVisible);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);

  const isLg = useMediaQuery(BREAKPOINTS.lg);
  const isMd = useMediaQuery(BREAKPOINTS.md);

  // Auto-collapse sidebar based on breakpoints
  useEffect(() => {
    if (!isLg) {
      setSidebarCollapsed(true);
    }
  }, [isLg, setSidebarCollapsed]);

  // Draggable divider state
  const [artifactWidth, setArtifactWidth] = useState(DEFAULT_ARTIFACT_WIDTH);
  const isDragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
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
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <div ref={containerRef} className="flex h-screen overflow-hidden bg-chat dark:bg-chat-dark">
      {/* Mobile menu button — visible below md */}
      {!isMd && (
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="fixed top-3 left-3 z-50 p-2 rounded-card bg-surface dark:bg-surface-dark border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark"
          aria-label="Toggle menu"
          title="菜单"
        >
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M3 5h12M3 9h12M3 13h12" />
          </svg>
        </button>
      )}

      {/* Mobile sidebar overlay */}
      {!isMd && mobileMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/40"
            onClick={() => setMobileMenuOpen(false)}
          />
          <div className="fixed inset-y-0 left-0 z-40 w-64 bg-panel dark:bg-panel-dark shadow-sidebar">
            {sidebar}
          </div>
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
            {sidebar}
          </div>
        </div>
      )}

      {/* Chat — takes remaining space */}
      <div className="flex-1 min-w-0 flex flex-col">{chat}</div>

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
              <div className="fixed inset-y-0 right-0 z-30 w-[85vw] max-w-lg bg-chat dark:bg-chat-dark border-l border-border dark:border-border-dark overflow-auto">
                {artifact}
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
