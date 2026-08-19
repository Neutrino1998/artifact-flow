'use client';

import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import ArtifactTree from './ArtifactTree';

export { groupArtifactsBySource } from './ArtifactTree';

export default function ArtifactList() {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const artifactsLoading = useArtifactStore((s) => s.artifactsLoading);
  const pendingUpdates = useArtifactStore((s) => s.pendingUpdates);
  const { selectArtifact } = useArtifacts();

  return (
    <ArtifactTree
      artifacts={artifacts}
      loading={artifactsLoading}
      pendingArtifactIds={pendingUpdates}
      onSelect={selectArtifact}
    />
  );
}
