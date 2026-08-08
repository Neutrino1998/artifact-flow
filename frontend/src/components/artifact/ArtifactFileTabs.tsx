'use client';

import { useEffect, useRef } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import { ArtifactFileIcon } from './ArtifactFileIcon';

const TAB_SCROLL_MARGIN = 8;

function scrollTabIntoView(tabList: HTMLDivElement, tab: HTMLDivElement) {
  const listRect = tabList.getBoundingClientRect();
  const tabRect = tab.getBoundingClientRect();

  let delta = 0;
  if (tabRect.left < listRect.left) {
    delta = tabRect.left - listRect.left - TAB_SCROLL_MARGIN;
  } else if (tabRect.right > listRect.right) {
    delta = tabRect.right - listRect.right + TAB_SCROLL_MARGIN;
  }

  if (delta !== 0) {
    tabList.scrollBy({ left: delta, behavior: 'smooth' });
  }
}

export default function ArtifactFileTabs() {
  const artifacts = useArtifactStore((s) => s.artifacts);
  const current = useArtifactStore((s) => s.current);
  const openArtifactIds = useArtifactStore((s) => s.openArtifactIds);
  const closeArtifactTab = useArtifactStore((s) => s.closeArtifactTab);
  const { selectArtifact } = useArtifacts();
  const tabListRef = useRef<HTMLDivElement>(null);
  const activeTabRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const tabList = tabListRef.current;
    const activeTab = activeTabRef.current;
    if (tabList && activeTab) scrollTabIntoView(tabList, activeTab);
  }, [current?.id, openArtifactIds.length]);

  const handleClose = (artifactId: string) => {
    const closingIndex = openArtifactIds.indexOf(artifactId);
    const wasActive = current?.id === artifactId;
    const nextId = wasActive
      ? openArtifactIds[closingIndex + 1] ?? openArtifactIds[closingIndex - 1]
      : undefined;

    closeArtifactTab(artifactId);
    if (nextId) selectArtifact(nextId);
  };

  if (openArtifactIds.length === 0) return null;

  return (
    <div
      ref={tabListRef}
      role="tablist"
      aria-label="已打开的文件"
      className="flex min-h-[45px] shrink-0 items-center gap-1 overflow-x-auto border-b border-border bg-panel-accent/50 px-2 py-1.5 dark:border-border-dark dark:bg-bg-dark"
    >
      {openArtifactIds.map((artifactId) => {
        const artifact =
          (current?.id === artifactId ? current : null) ??
          artifacts.find((item) => item.id === artifactId);
        const active = current?.id === artifactId;
        const title = artifact?.original_filename || artifact?.title || artifactId;
        const contentType = artifact?.content_type ?? 'text/plain';

        return (
          <div
            key={artifactId}
            ref={active ? activeTabRef : undefined}
            className={`group relative flex h-10 min-w-[7rem] max-w-[10.5rem] shrink-0 items-center rounded-lg border sm:h-8 ${
              active
                ? 'border-transparent bg-surface text-text-primary shadow-sm dark:bg-surface-dark dark:text-text-primary-dark'
                : 'border-transparent text-text-secondary hover:bg-surface/60 dark:text-text-secondary-dark dark:hover:bg-surface-dark/60'
            }`}
          >
            {active && (
              <span
                aria-hidden="true"
                className="absolute bottom-1.5 left-0 top-1.5 w-0.5 rounded-r-full bg-accent"
              />
            )}
            <button
              type="button"
              role="tab"
              aria-selected={active}
              title={title}
              onClick={() => {
                if (!active) selectArtifact(artifactId);
              }}
              className="flex h-full min-w-0 flex-1 items-center gap-1.5 rounded-l-lg pl-2 text-left"
            >
              <ArtifactFileIcon contentType={contentType} filename={title} compact />
              <span className="min-w-0 flex-1 truncate text-xs">{title}</span>
            </button>
            <button
              type="button"
              onClick={() => handleClose(artifactId)}
              className="mr-1 flex h-8 w-7 shrink-0 items-center justify-center rounded-md text-base leading-none text-text-tertiary opacity-70 hover:bg-text-primary/5 hover:text-text-primary group-hover:opacity-100 dark:text-text-tertiary-dark dark:hover:bg-text-primary-dark/10 dark:hover:text-text-primary-dark sm:h-6 sm:w-5"
              aria-label={`关闭 ${title}`}
              title="关闭"
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}
