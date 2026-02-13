'use client';

import { useEffect } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import ArtifactToolbar from './ArtifactToolbar';
import ArtifactTabs from './ArtifactTabs';
import ArtifactList from './ArtifactList';
import MarkdownPreview from './MarkdownPreview';
import SourceView from './SourceView';
import DiffView from './DiffView';

export default function ArtifactPanel() {
  const current = useArtifactStore((s) => s.current);
  const currentLoading = useArtifactStore((s) => s.currentLoading);
  const viewMode = useArtifactStore((s) => s.viewMode);
  const selectedVersion = useArtifactStore((s) => s.selectedVersion);
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const { loadArtifacts } = useArtifacts();

  const setCurrent_ = useArtifactStore((s) => s.setCurrent);

  // Reload artifacts when conversation session changes
  useEffect(() => {
    // Clear stale artifact detail from previous session
    setCurrent_(null);
    if (sessionId) {
      loadArtifacts();
    }
  }, [sessionId, setCurrent_, loadArtifacts]);

  if (currentLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-surface dark:bg-surface-dark">
        <span className="text-sm text-text-tertiary dark:text-text-tertiary-dark">
          加载文稿中...
        </span>
      </div>
    );
  }

  if (!current) {
    return <ArtifactList />;
  }

  const content = selectedVersion?.content ?? current.content;

  return (
    <div className="h-full flex flex-col bg-surface dark:bg-surface-dark">
      <ArtifactToolbar />
      <ArtifactTabs />
      <div className="flex-1 overflow-auto">
        {viewMode === 'preview' && <MarkdownPreview content={content} />}
        {viewMode === 'source' && <SourceView content={content} />}
        {viewMode === 'diff' && (
          <DiffView changes={selectedVersion?.changes ?? null} />
        )}
      </div>
    </div>
  );
}
