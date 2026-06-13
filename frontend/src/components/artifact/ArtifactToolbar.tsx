'use client';

import { useCallback } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useStreamStore } from '@/stores/streamStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { fetchArtifactRawObjectUrl } from '@/lib/api';
import ArtifactTabs from './ArtifactTabs';

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
  const { copied, copy } = useCopyFeedback();

  const handleCopy = useCallback(() => {
    copy(selectedVersion?.content ?? current?.content ?? '');
  }, [current, selectedVersion, copy]);

  const handleDownload = useCallback(async () => {
    if (!current) return;
    // Blob-backed artifact (image / docx / pdf upload): download the immutable
    // original via /raw (text path would emit an empty file — there is no text
    // representation). Hidden while streaming, so the blob is always flushed here.
    if (current.has_blob) {
      try {
        const url = await fetchArtifactRawObjectUrl(current.session_id, current.id);
        const a = document.createElement('a');
        a.href = url;
        a.download = current.original_filename ?? current.title;
        a.click();
        URL.revokeObjectURL(url);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Download failed';
        window.alert(message);
      }
      return;
    }
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
    <>
      {/* Title row */}
      <div className="px-4 py-2 border-b border-border dark:border-border-dark min-w-0">
        <h3 className="font-semibold text-text-primary dark:text-text-primary-dark truncate">
          {current.title}
        </h3>
      </div>

      {/* Tabs + actions row */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-border dark:border-border-dark">
        <ArtifactTabs />

        <div className="flex items-center gap-1">
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

          {/* Refresh — hidden during streaming: it re-reads via REST (pure DB now,
              lags the live event stream) and would clobber live content with a
              stale snapshot. Live content auto-refreshes from ARTIFACT_* events. */}
          {!isStreaming && (
            <button
              onClick={handleRefresh}
              className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
              aria-label="Refresh artifact"
              title="刷新"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M1.5 7a5.5 5.5 0 0 1 9.37-3.9M12.5 7a5.5 5.5 0 0 1-9.37 3.9" />
                <path d="M11 1v2.5h-2.5M3 11v-2.5h2.5" />
              </svg>
            </button>
          )}

          {/* Copy — hidden for blob-backed artifacts (no text content to copy) */}
          {!current.has_blob && (
          <button
            onClick={handleCopy}
            className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
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
          )}

          {/* Download — hidden during streaming (decision 6): a durable-acting
              read. Text download would emit live-but-uncommitted content; blob
              raw download would 404 (blob not flushed until turn end). Re-enabled
              after COMPLETE, when the DB re-pull has aligned everything. */}
          {!isStreaming && (
            <button
              onClick={handleDownload}
              className="p-1.5 rounded text-text-secondary dark:text-text-secondary-dark hover:bg-surface dark:hover:bg-bg-dark transition-colors"
              aria-label="Download artifact"
              title="下载"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M7 2v7.5M4 7l3 3 3-3M2.5 11.5h9" />
              </svg>
            </button>
          )}

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
    </>
  );
}
