'use client';

import { useMemo } from 'react';
import { useArtifactStore, type ArtifactViewMode } from '@/stores/artifactStore';

const allTabs: { mode: ArtifactViewMode; label: string }[] = [
  { mode: 'preview', label: 'Preview' },
  { mode: 'source', label: 'Source' },
  { mode: 'diff', label: 'Diff' },
];

export default function ArtifactTabs() {
  const viewMode = useArtifactStore((s) => s.viewMode);
  const setViewMode = useArtifactStore((s) => s.setViewMode);
  const contentType = useArtifactStore((s) => s.current?.content_type);
  const hasBlob = useArtifactStore((s) => s.current?.has_blob);

  const tabs = useMemo(() => {
    // markdown + html get the rich Preview alongside Source/Diff (html via a
    // static sandboxed iframe). Other text types are Source/Diff only.
    if (contentType === 'text/markdown' || contentType === 'text/html') return allTabs;
    // blob 类(图片/二进制)只有 preview(无文本 source/diff)
    if (hasBlob || contentType?.startsWith('image/')) {
      return allTabs.filter((t) => t.mode === 'preview');
    }
    return allTabs.filter((t) => t.mode !== 'preview');
  }, [contentType, hasBlob]);

  return (
    <div className="inline-flex p-0.5 rounded-lg bg-panel-accent dark:bg-surface-dark text-xs">
      {tabs.map(({ mode, label }) => (
        <button
          key={mode}
          onClick={() => setViewMode(mode)}
          className={`px-3 py-1 rounded-md transition-colors ${
            viewMode === mode
              ? 'bg-surface dark:bg-bg-dark text-accent font-medium shadow-sm'
              : 'text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
