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
    <div className="flex border-b border-border dark:border-border-dark">
      {tabs.map(({ mode, label }) => (
        <button
          key={mode}
          onClick={() => setViewMode(mode)}
          className={`px-4 py-2 text-xs font-medium transition-colors ${
            viewMode === mode
              ? 'text-accent border-b-2 border-accent'
              : 'text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
