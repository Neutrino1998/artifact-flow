'use client';

import { useEffect } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import ArtifactToolbar from './ArtifactToolbar';
import ArtifactList from './ArtifactList';
import MarkdownPreview from './MarkdownPreview';
import SourceView from './SourceView';
import DiffView from './DiffView';
import ImagePreview from './ImagePreview';

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

  if (currentLoading) {
    return (
      <div className="h-full flex items-center justify-center bg-chat dark:bg-chat-dark">
        <span className="text-text-tertiary dark:text-text-tertiary-dark">
          加载文稿中...
        </span>
      </div>
    );
  }

  if (!current) {
    return <ArtifactList />;
  }

  const content = selectedVersion?.content ?? current.content;
  // 图片 artifact 无文本内容,preview 走 ImagePreview(authed fetch /raw → objectURL)。
  const isImage = (current.content_type ?? '').startsWith('image/');
  const imgSession = current.session_id || sessionId || '';

  return (
    <div className="h-full flex flex-col bg-chat dark:bg-chat-dark">
      <ArtifactToolbar />
      <div className="flex-1 overflow-auto">
        {viewMode === 'preview' && (isImage
          ? <ImagePreview
              sessionId={imgSession}
              artifactId={current.id}
              // updated_at: '' while live (mid-turn upload 404s) → real timestamp on the
              // COMPLETE DB re-pull, re-firing the fetch so the image finally resolves.
              refreshKey={current.updated_at || undefined}
            />
          : <MarkdownPreview content={content} />)}
        {viewMode === 'source' && <SourceView content={content} />}
        {viewMode === 'diff' && (
          <DiffView
            oldContent={diffBaseContent ?? ''}
            newContent={selectedVersion?.content ?? content}
          />
        )}
      </div>
    </div>
  );
}
