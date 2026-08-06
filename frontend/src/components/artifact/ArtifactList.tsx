'use client';

import { useMemo, useState } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import { parseUtcIso } from '@/lib/time';
import type { ArtifactSummary } from '@/types';
import {
  ArtifactBrowserIcon,
  ArtifactFileIcon,
  ArtifactFolderIcon,
  artifactFileTypeLabel,
} from './ArtifactFileIcon';

const SOURCE_GROUPS = [
  { key: 'agent', label: 'Agent' },
  { key: 'user_upload', label: 'Uploads' },
  { key: 'sandbox', label: 'Sandbox' },
  { key: 'tool', label: 'Tools' },
] as const;

type SourceGroupKey = (typeof SOURCE_GROUPS)[number]['key'] | 'other';

export function groupArtifactsBySource(artifacts: ArtifactSummary[]) {
  const buckets = new Map<SourceGroupKey, ArtifactSummary[]>();
  for (const artifact of artifacts) {
    const source = artifact.source?.toLowerCase();
    const key: SourceGroupKey = SOURCE_GROUPS.some((group) => group.key === source)
      ? (source as SourceGroupKey)
      : 'other';
    const bucket = buckets.get(key) ?? [];
    bucket.push(artifact);
    buckets.set(key, bucket);
  }

  return [
    ...SOURCE_GROUPS.map((group) => ({
      ...group,
      artifacts: buckets.get(group.key) ?? [],
    })),
    { key: 'other' as const, label: 'Other', artifacts: buckets.get('other') ?? [] },
  ].filter((group) => group.artifacts.length > 0);
}

function artifactTooltip(artifact: ArtifactSummary): string {
  const parts = [artifact.content_type];
  if (artifact.updated_at) {
    parts.push(parseUtcIso(artifact.updated_at).toLocaleDateString());
  }
  return parts.join(' · ');
}

export default function ArtifactList() {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const artifactsLoading = useArtifactStore((s) => s.artifactsLoading);
  const pendingUpdates = useArtifactStore((s) => s.pendingUpdates);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const { selectArtifact } = useArtifacts();
  const groups = useMemo(() => groupArtifactsBySource(artifacts), [artifacts]);

  if (artifactsLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-chat dark:bg-chat-dark">
        <span className="text-text-tertiary dark:text-text-tertiary-dark">
          加载文件中...
        </span>
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <div className="h-full flex items-center justify-center bg-chat dark:bg-chat-dark">
        <span className="text-text-tertiary dark:text-text-tertiary-dark">
          暂无文件
        </span>
      </div>
    );
  }

  const toggleGroup = (key: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-chat dark:bg-chat-dark">
      <div className="shrink-0 border-b border-border px-4 py-3 dark:border-border-dark">
        <div className="flex items-center gap-2">
          <span className="text-text-primary dark:text-text-primary-dark">
            <ArtifactBrowserIcon size={16} />
          </span>
          <h3 className="font-semibold text-text-primary dark:text-text-primary-dark">
            文件面板
          </h3>
        </div>
        <p className="mt-0.5 text-xs text-text-tertiary dark:text-text-tertiary-dark">
          {artifacts.length} 个文件 · 按来源分组
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        {groups.map((group) => {
          const isCollapsed = collapsed.has(group.key);
          const groupId = `artifact-source-${group.key}`;
          return (
            <section key={group.key} className="mb-1" aria-labelledby={`${groupId}-label`}>
              <button
                type="button"
                onClick={() => toggleGroup(group.key)}
                className="flex h-9 w-full items-center gap-2 rounded-lg px-2 text-left text-sm font-medium text-text-primary hover:bg-white/50 dark:text-text-primary-dark dark:hover:bg-black/25"
                aria-expanded={!isCollapsed}
                aria-controls={groupId}
              >
                <span aria-hidden="true" className="flex w-3.5 shrink-0 justify-center">
                  <span
                    className={`h-2 w-2 border-b border-r border-text-tertiary transition-transform dark:border-text-tertiary-dark ${
                      isCollapsed ? '-rotate-45' : 'rotate-45'
                    }`}
                  />
                </span>
                <ArtifactFolderIcon />
                <span id={`${groupId}-label`} className="min-w-0 flex-1 truncate">
                  {group.label}
                </span>
                <span className="text-xs font-normal text-text-tertiary dark:text-text-tertiary-dark">
                  {group.artifacts.length}
                </span>
              </button>

              {!isCollapsed && (
                <div id={groupId} className="ml-[15px] border-l border-border pl-1.5 dark:border-border-dark">
                  {group.artifacts.map((artifact) => {
                    const hasPending = pendingUpdates.includes(artifact.id);
                    const displayName = artifact.original_filename || artifact.title;
                    const fileType = artifactFileTypeLabel(
                      artifact.content_type,
                      displayName,
                    );
                    return (
                      <button
                        key={artifact.id}
                        type="button"
                        onClick={() => selectArtifact(artifact.id)}
                        className="flex h-10 w-full items-center gap-2 rounded-lg px-2 text-left text-text-secondary hover:bg-white/50 hover:text-text-primary dark:text-text-secondary-dark dark:hover:bg-black/25 dark:hover:text-text-primary-dark"
                        title={artifactTooltip(artifact)}
                      >
                        <ArtifactFileIcon
                          contentType={artifact.content_type}
                          filename={displayName}
                        />
                        <span className="min-w-0 flex-1 truncate text-sm">
                          {displayName}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] font-medium tracking-wide text-text-tertiary dark:text-text-tertiary-dark">
                          {fileType}
                        </span>
                        {hasPending && (
                          <span
                            className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent"
                            title="本回合已更新"
                            aria-label="本回合已更新"
                          />
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
