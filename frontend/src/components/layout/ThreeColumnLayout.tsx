'use client';

import { useCallback, useRef, useEffect, useState } from 'react';
import { useUIStore } from '@/stores/uiStore';

const MIN_CHAT_WIDTH = 400;
const MIN_ARTIFACT_WIDTH = 300;
const DEFAULT_ARTIFACT_WIDTH = 480;

interface ThreeColumnLayoutProps {
  sidebar: React.ReactNode;
  chat: React.ReactNode;
  artifact?: React.ReactNode;
}

export default function ThreeColumnLayout({
  sidebar,
  chat,
  artifact,
}: ThreeColumnLayoutProps) {
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const artifactPanelVisible = useUIStore((s) => s.artifactPanelVisible);

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
      const sidebarWidth = sidebarCollapsed ? 48 : 256;
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
  }, [sidebarCollapsed]);

  const handleDoubleClick = useCallback(() => {
    setArtifactWidth(DEFAULT_ARTIFACT_WIDTH);
  }, []);

  const showArtifact = artifactPanelVisible && artifact;

  return (
    <div ref={containerRef} className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <div
        className={`flex-shrink-0 transition-[width] duration-150 ease-out overflow-hidden border-r border-border dark:border-border-dark ${
          sidebarCollapsed ? 'w-12' : 'w-64'
        }`}
      >
        <div className={`${sidebarCollapsed ? 'w-12' : 'w-64'} h-full`}>
          {sidebar}
        </div>
      </div>

      {/* Chat — takes remaining space */}
      <div className="flex-1 min-w-0 flex flex-col">{chat}</div>

      {/* Artifact panel with draggable divider */}
      {showArtifact && (
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
    </div>
  );
}
