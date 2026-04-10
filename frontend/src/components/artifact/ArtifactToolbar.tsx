'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import { exportArtifact } from '@/lib/api';

function getFileExtension(contentType: string): string {
  const map: Record<string, string> = {
    'text/markdown': '.md',
    'text/plain': '.txt',
    'text/html': '.html',
    'text/css': '.css',
    'text/csv': '.csv',
    'application/json': '.json',
    'application/javascript': '.js',
    'text/javascript': '.js',
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
  const isStreaming = useStreamStore((s) => s.isStreaming);
  const { selectVersion, selectArtifact } = useArtifacts();
  const [copied, setCopied] = useState(false);
  const [showDownloadMenu, setShowDownloadMenu] = useState(false);
  const downloadMenuRef = useRef<HTMLDivElement>(null);

  // Close download menu on outside click
  useEffect(() => {
    if (!showDownloadMenu) return;
    const handler = (e: MouseEvent) => {
      if (downloadMenuRef.current && !downloadMenuRef.current.contains(e.target as Node)) {
        setShowDownloadMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showDownloadMenu]);

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
    setShowDownloadMenu(false);
  }, [current, selectedVersion]);

  const handleExportDocx = useCallback(async () => {
    if (!current) return;
    setShowDownloadMenu(false);

    try {
      const blob = await exportArtifact(current.session_id, current.id, 'docx');
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = current.title.replace(/[/\\?%*:|"<>]/g, '-') + '.docx';
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Export failed';
      window.alert(message);
    }
  }, [current]);

  const handleRefresh = useCallback(() => {
    if (!current) return;
    selectArtifact(current.id);
  }, [current, selectArtifact]);

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
  const isMarkdown = current.content_type === 'text/markdown';

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-border dark:border-border-dark">
      <div className="flex items-center gap-3 min-w-0">
        <h3 className="font-semibold text-text-primary dark:text-text-primary-dark truncate">
          {current.title}
        </h3>
        {/* Version selector — hidden during streaming (in-memory versions not in DB yet) */}
        {!isStreaming && versions.length > 1 && (
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
          title="刷新"
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
          title={copied ? '已复制' : '复制内容'}
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

        {/* Download (dropdown) */}
        <div className="relative" ref={downloadMenuRef}>
          <button
            onClick={() => setShowDownloadMenu((v) => !v)}
            className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            aria-label="Download artifact"
            title="下载"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M7 2v7.5M4 7l3 3 3-3M2.5 11.5h9" />
            </svg>
          </button>

          {showDownloadMenu && (
            <div className="absolute right-0 top-full mt-1 bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-lg shadow-float py-1 z-50 min-w-[160px]">
              <button
                onClick={handleDownload}
                className="w-full text-left px-3 py-1.5 text-xs text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
              >
                下载原格式
              </button>
              {isMarkdown && (
                <button
                  onClick={handleExportDocx}
                  className="w-full text-left px-3 py-1.5 text-xs text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
                >
                  导出为 Word (.docx)
                </button>
              )}
            </div>
          )}
        </div>

        {/* Back to list */}
        <button
          onClick={() => setCurrent(null)}
          className="ml-2 w-7 h-7 flex items-center justify-center rounded-full bg-accent text-white hover:bg-accent-hover transition-colors"
          aria-label="Back to artifact list"
          title="返回列表"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 3.5h10M2 7h10M2 10.5h10" />
          </svg>
        </button>
      </div>
    </div>
  );
}
