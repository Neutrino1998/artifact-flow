'use client';

import { useState, useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useUIStore } from '@/stores/uiStore';
import { useArtifacts } from '@/hooks/useArtifacts';

function getFileExtension(contentType: string): string {
  const map: Record<string, string> = {
    'text/markdown': '.md',
    'text/plain': '.txt',
    'text/html': '.html',
    'text/css': '.css',
    'text/csv': '.csv',
    'application/json': '.json',
    'application/javascript': '.js',
    'text/x-python': '.py',
    'text/x-typescript': '.ts',
  };
  return map[contentType] ?? '.txt';
}

export default function ArtifactToolbar() {
  const current = useArtifactStore((s) => s.current);
  const versions = useArtifactStore((s) => s.versions);
  const selectedVersion = useArtifactStore((s) => s.selectedVersion);
  const setCurrent = useArtifactStore((s) => s.setCurrent);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const { selectVersion, selectArtifact } = useArtifacts();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    const content = selectedVersion?.content ?? current?.content ?? '';
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [current, selectedVersion]);

  const handleDownload = useCallback(() => {
    if (!current) return;
    const content = selectedVersion?.content ?? current.content;
    const ext = getFileExtension(current.content_type);
    const filename = current.title.replace(/[/\\?%*:|"<>]/g, '-') + ext;
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, [current, selectedVersion]);

  const handleRefresh = useCallback(() => {
    if (!current) return;
    selectArtifact(current.id);
  }, [current, selectArtifact]);

  const handleClose = useCallback(() => {
    setCurrent(null);
    setArtifactPanelVisible(false);
  }, [setCurrent, setArtifactPanelVisible]);

  const handleVersionChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      if (!current) return;
      const v = parseInt(e.target.value, 10);
      selectVersion(current.id, v);
    },
    [current, selectVersion]
  );

  if (!current) return null;

  const displayVersion = selectedVersion?.version ?? current.current_version;

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-border dark:border-border-dark">
      <div className="flex items-center gap-3 min-w-0">
        <h3 className="text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
          {current.title}
        </h3>
        {/* Version selector */}
        {versions.length > 1 && (
          <select
            value={displayVersion}
            onChange={handleVersionChange}
            className="text-xs bg-bg dark:bg-bg-dark border border-border dark:border-border-dark rounded px-1.5 py-0.5 text-text-secondary dark:text-text-secondary-dark"
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} ({v.update_type})
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="flex items-center gap-1">
        {/* Refresh */}
        <button
          onClick={handleRefresh}
          className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Refresh artifact"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M1.5 7a5.5 5.5 0 0 1 9.37-3.9M12.5 7a5.5 5.5 0 0 1-9.37 3.9" />
            <path d="M11 1v2.5h-2.5M3 11v-2.5h2.5" />
          </svg>
        </button>

        {/* Copy */}
        <button
          onClick={handleCopy}
          className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Copy content"
        >
          {copied ? (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 7.5 6 10.5l5-7" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="4.5" y="4.5" width="7" height="7" rx="1" />
              <path d="M9.5 4.5V3a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v5.5a1 1 0 0 0 1 1h1.5" />
            </svg>
          )}
        </button>

        {/* Download */}
        <button
          onClick={handleDownload}
          className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Download artifact"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M7 2v7.5M4 7l3 3 3-3M2.5 11.5h9" />
          </svg>
        </button>

        {/* Back to list */}
        <button
          onClick={() => setCurrent(null)}
          className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Back to artifact list"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 3.5h10M2 7h10M2 10.5h10" />
          </svg>
        </button>

        {/* Close */}
        <button
          onClick={handleClose}
          className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
          aria-label="Close artifact panel"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M3 3l8 8M11 3l-8 8" />
          </svg>
        </button>
      </div>
    </div>
  );
}
