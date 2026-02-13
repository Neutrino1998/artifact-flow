'use client';

import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifacts } from '@/hooks/useArtifacts';

export default function ArtifactList() {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const artifactsLoading = useArtifactStore((s) => s.artifactsLoading);
  const pendingUpdates = useArtifactStore((s) => s.pendingUpdates);
  const { selectArtifact } = useArtifacts();

  if (artifactsLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-surface dark:bg-surface-dark">
        <span className="text-sm text-text-tertiary dark:text-text-tertiary-dark">
          加载文稿中...
        </span>
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <div className="h-full flex items-center justify-center bg-surface dark:bg-surface-dark">
        <span className="text-sm text-text-tertiary dark:text-text-tertiary-dark">
          暂无文稿
        </span>
      </div>
    );
  }

  return (
    <div className="h-full bg-surface dark:bg-surface-dark">
      <div className="px-4 py-3 border-b border-border dark:border-border-dark">
        <h3 className="text-sm font-semibold text-text-primary dark:text-text-primary-dark">
          文稿
        </h3>
      </div>
      <div className="overflow-y-auto">
        {artifacts.map((artifact) => {
          const hasPending = pendingUpdates.includes(artifact.id);
          return (
            <button
              key={artifact.id}
              onClick={() => selectArtifact(artifact.id)}
              className="w-full text-left px-4 py-3 border-b border-border dark:border-border-dark hover:bg-bg dark:hover:bg-bg-dark transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm text-text-primary dark:text-text-primary-dark flex-1 truncate">
                  {artifact.title}
                </span>
                {hasPending && (
                  <span className="w-2 h-2 rounded-full bg-red-500 flex-shrink-0" />
                )}
              </div>
              <div className="flex items-center gap-2 mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
                <span className="px-1.5 py-0.5 bg-bg dark:bg-bg-dark rounded text-[10px]">
                  {artifact.content_type}
                </span>
                <span>v{artifact.current_version}</span>
                <span>{new Date(artifact.updated_at).toLocaleDateString()}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
