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

  const tabs = useMemo(() => {
    if (contentType === 'text/markdown') return allTabs;
    return allTabs.filter((t) => t.mode !== 'preview');
  }, [contentType]);

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
