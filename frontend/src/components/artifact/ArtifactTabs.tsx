'use client';

import { useMemo } from 'react';
import { useArtifactStore, type ArtifactViewMode } from '@/stores/artifactStore';
import { SegmentedTabs } from '@/components/ui/SegmentedTabs';
import { isCsvMime } from '@/lib/artifactPreview';

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
    // Markdown / HTML / CSV get rich Preview alongside Source/Diff.
    // Other text types are Source/Diff only.
    if (contentType === 'text/markdown' || contentType === 'text/html' || isCsvMime(contentType)) return allTabs;
    // blob 类(图片/二进制)只有 preview(无文本 source/diff)
    if (hasBlob || contentType?.startsWith('image/')) {
      return allTabs.filter((t) => t.mode === 'preview');
    }
    return allTabs.filter((t) => t.mode !== 'preview');
  }, [contentType, hasBlob]);

  return (
    <SegmentedTabs
      ariaLabel="Artifact view"
      value={viewMode}
      options={tabs.map(({ mode, label }) => ({ value: mode, label }))}
      onChange={setViewMode}
    />
  );
}
