'use client';

import { useEffect } from 'react';
import { useArtifactStore } from '@/stores/artifactStore';
import { useConversationStore } from '@/stores/conversationStore';
import { useArtifacts } from '@/hooks/useArtifacts';
import ArtifactToolbar from './ArtifactToolbar';
import ArtifactList from './ArtifactList';
import MarkdownPreview from './MarkdownPreview';
import HtmlPreview from './HtmlPreview';
import SourceView from './SourceView';
import DiffView from './DiffView';
import ImagePreview from './ImagePreview';
import BinaryFilePreview from './BinaryFilePreview';
import CsvPreview from './CsvPreview';
import DocxPreview from './DocxPreview';
import PdfPreview from './PdfPreview';
import SpreadsheetPreview from './SpreadsheetPreview';
import { isSafeInlineImageMime } from '@/lib/mime';
import { isCsvMime, isDocxMime, isPdfMime, isSpreadsheetMime } from '@/lib/artifactPreview';

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
          加载文件中...
        </span>
      </div>
    );
  }

  if (!current) {
    return <ArtifactList />;
  }

  const content = selectedVersion?.content ?? current.content;
  // blob 类 artifact 无文本内容:图片 preview 走 ImagePreview(authed fetch /raw →
  // objectURL),其它二进制(docx/pdf 上传,C-0 blob-only)走 BinaryFilePreview(下载卡片)。
  const isImage = isSafeInlineImageMime(current.content_type);
  const isBinary = !!current.has_blob && !isImage;
  // text/html → static sandboxed iframe preview (no JS, no external resources);
  // Source/Diff tabs stay available as a fallback. See HtmlPreview for the model.
  const isHtml = current.content_type === 'text/html';
  const imgSession = current.session_id || sessionId || '';

  return (
    <div className="h-full flex flex-col bg-chat dark:bg-chat-dark">
      <ArtifactToolbar />
      <div className="flex-1 overflow-auto">
        {viewMode === 'preview' && (isImage
          ? <ImagePreview
              sessionId={imgSession}
              artifactId={current.id}
              // While the turn runs, render from the send-local preview File matched
              // by this name (instant, no /raw 404 before flush); cleared at terminal → falls to /raw.
              originalFilename={current.original_filename}
              // updated_at: '' while live → real timestamp on the COMPLETE DB re-pull,
              // re-firing the effect so the image resolves from the DB blob.
              refreshKey={current.updated_at || undefined}
            />
          : isCsvMime(current.content_type)
          ? <CsvPreview
              content={content}
              sessionId={imgSession}
              artifactId={current.id}
              originalFilename={current.original_filename}
              contentType={current.content_type}
              hasBlob={!!current.has_blob}
            />
          : isBinary && isPdfMime(current.content_type)
          ? <PdfPreview
              sessionId={imgSession}
              artifactId={current.id}
              originalFilename={current.original_filename}
              contentType={current.content_type}
            />
          : isBinary && isDocxMime(current.content_type)
          ? <DocxPreview
              sessionId={imgSession}
              artifactId={current.id}
              originalFilename={current.original_filename}
              contentType={current.content_type}
            />
          : isBinary && isSpreadsheetMime(current.content_type)
          ? <SpreadsheetPreview
              sessionId={imgSession}
              artifactId={current.id}
              originalFilename={current.original_filename}
              contentType={current.content_type}
            />
          : isBinary
          ? <BinaryFilePreview
              sessionId={imgSession}
              artifactId={current.id}
              originalFilename={current.original_filename}
              contentType={current.content_type}
            />
          : isHtml
          ? <HtmlPreview content={content} />
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
