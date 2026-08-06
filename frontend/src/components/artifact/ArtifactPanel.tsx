'use client';

import { useEffect } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import ArtifactToolbar from './ArtifactToolbar';
import ArtifactList from './ArtifactList';
import ArtifactFileTabs from './ArtifactFileTabs';
import SourceView from './SourceView';
import DiffView from './DiffView';
import ArtifactPreviewContent from './ArtifactPreviewContent';

export default function ArtifactPanel() {
  const current = useArtifactStore((s) => s.current);
  const currentLoading = useArtifactStore((s) => s.currentLoading);
  const viewMode = useArtifactStore((s) => s.viewMode);
  const selectedVersion = useArtifactStore((s) => s.selectedVersion);
  const diffBaseContent = useArtifactStore((s) => s.diffBaseContent);
  const sessionId = useConversationStore((s) => s.current?.session_id);
  const { loadArtifacts } = useArtifacts();

  const setCurrent_ = useArtifactStore((s) => s.setCurrent);

  // Reload artifacts when conversation session changes
  useEffect(() => {
    // Clear stale artifact detail only if it belongs to a different session
    const cur = useArtifactStore.getState().current;
    if (cur && cur.session_id !== sessionId) {
      setCurrent_(null);
    }
    if (sessionId) {
      loadArtifacts();
    }
  }, [sessionId, setCurrent_, loadArtifacts]);

  if (currentLoading && !current) {
    return (
      <div className="h-full flex items-center justify-center bg-chat dark:bg-chat-dark">
        <span className="text-text-tertiary dark:text-text-tertiary-dark">
          加载文件中...
        </span>
      </div>
    );
  }

  if (!current) {
    return <ArtifactList />;
  }

  const content = selectedVersion?.content ?? current.content;
  const imgSession = current.session_id || sessionId || '';

  return (
    <div className="h-full flex flex-col bg-chat dark:bg-chat-dark">
      <ArtifactFileTabs />
      {currentLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <span className="text-text-tertiary dark:text-text-tertiary-dark">
            加载文件中...
          </span>
        </div>
      ) : (
        <>
          <ArtifactToolbar />
          <div className="flex-1 overflow-auto">
            {viewMode === 'preview' && (
              <ArtifactPreviewContent
                sessionId={imgSession}
                artifactId={current.id}
                content={content}
                contentType={current.content_type}
                hasBlob={!!current.has_blob}
                originalFilename={current.original_filename}
                // updated_at: '' while live → real timestamp on the COMPLETE DB re-pull,
                // re-firing the effect so the image resolves from the DB blob.
                refreshKey={current.updated_at || undefined}
              />
            )}
            {viewMode === 'source' && <SourceView content={content} />}
            {viewMode === 'diff' && (
              <DiffView
                oldContent={diffBaseContent ?? ''}
                newContent={selectedVersion?.content ?? content}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
