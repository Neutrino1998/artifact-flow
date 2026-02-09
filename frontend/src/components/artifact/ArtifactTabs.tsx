'use client';

import { useArtifactStore, type ArtifactViewMode } from '@/stores/artifactStore';

const tabs: { mode: ArtifactViewMode; label: string }[] = [
  { mode: 'preview', label: 'Preview' },
  { mode: 'source', label: 'Source' },
  { mode: 'diff', label: 'Diff' },
];

export default function ArtifactTabs() {
  const viewMode = useArtifactStore((s) => s.viewMode);
  const setViewMode = useArtifactStore((s) => s.setViewMode);

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
