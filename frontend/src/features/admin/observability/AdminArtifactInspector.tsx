'use client';

import { useCallback, useEffect, useState } from 'react';
import ArtifactPreviewContent from '@/components/artifact/ArtifactPreviewContent';
import { SELECT_CHEVRON_COMPACT } from '@/components/ui/SelectChevron';
import * as api from '@/lib/api';
import { isCsvMime } from '@/lib/artifactPreview';
import {
  getTextArtifactDownloadFilename,
  triggerBlobDownload,
  triggerObjectUrlDownload,
} from '@/lib/download';
import { BUTTON_SECONDARY, SELECT_COMPACT } from '@/lib/styles';
import { parseUtcIso } from '@/lib/time';
import type { ArtifactDetail, ArtifactSummary, VersionDetail } from '@/types';

function shouldUseAdminArtifactPreview(detail: ArtifactDetail): boolean {
  if (detail.has_blob) return true;
  return detail.content_type === 'text/markdown'
    || detail.content_type === 'text/html'
    || isCsvMime(detail.content_type);
}

/**
 * Read-only artifact forensics for the administrator-selected conversation.
 *
 * This deliberately uses admin APIs and local component state rather than the
 * user artifact store: inspecting another user's durable snapshot must not
 * change the current user's artifact selection or live SSE state. Only the
 * MIME-based ArtifactPreviewContent renderer is shared with the user feature.
 */
export default function AdminArtifactInspector({
  conversationId,
  refreshTick,
}: {
  conversationId: string;
  refreshTick: number;
}) {
  const [list, setList] = useState<ArtifactSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [versionContent, setVersionContent] = useState<VersionDetail | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const [downloadLoading, setDownloadLoading] = useState(false);

  useEffect(() => {
    setList(null);
    setSelectedId(null);
    setDetail(null);
    setViewingVersion(null);
    setVersionContent(null);
    let cancelled = false;
    setListLoading(true);
    api.listAdminConversationArtifacts(conversationId).then((response) => {
      if (!cancelled) setList(response.artifacts);
    }).catch((error) => {
      if (!cancelled) {
        console.error('Failed to load artifacts:', error);
        setList([]);
      }
    }).finally(() => {
      if (!cancelled) setListLoading(false);
    });
    return () => { cancelled = true; };
  }, [conversationId, refreshTick]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setViewingVersion(null);
      setVersionContent(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetail(null);
    setViewingVersion(null);
    setVersionContent(null);
    api.getAdminConversationArtifact(conversationId, selectedId).then((response) => {
      if (!cancelled) {
        setDetail(response);
        setViewingVersion(response.current_version);
      }
    }).catch((error) => {
      if (!cancelled) {
        console.error('Failed to load artifact:', error);
        setDetail(null);
      }
    }).finally(() => {
      if (!cancelled) setDetailLoading(false);
    });
    return () => { cancelled = true; };
  }, [conversationId, selectedId]);

  useEffect(() => {
    if (selectedId == null || detail == null || viewingVersion == null) {
      setVersionContent(null);
      setVersionLoading(false);
      return;
    }
    if (viewingVersion === detail.current_version) {
      setVersionContent(null);
      setVersionLoading(false);
      return;
    }
    let cancelled = false;
    setVersionContent(null);
    setVersionLoading(true);
    api.getAdminConversationArtifactVersion(
      conversationId,
      selectedId,
      viewingVersion,
    ).then((response) => {
      if (!cancelled) setVersionContent(response);
    }).catch((error) => {
      if (!cancelled) {
        console.error('Failed to load version:', error);
        setVersionContent(null);
      }
    }).finally(() => {
      if (!cancelled) setVersionLoading(false);
    });
    return () => { cancelled = true; };
  }, [conversationId, selectedId, detail, viewingVersion]);

  const isViewingCurrent = detail != null
    && viewingVersion != null
    && viewingVersion === detail.current_version;
  const versionContentMatches = versionContent != null
    && versionContent.version === viewingVersion;
  const versionContentReady = isViewingCurrent || versionContentMatches;
  const displayedContent = isViewingCurrent
    ? detail?.content ?? ''
    : versionContentMatches
      ? versionContent.content
      : '';
  const showingRichPreview = detail != null
    && versionContentReady
    && shouldUseAdminArtifactPreview(detail);
  const contentClassName = showingRichPreview
    ? detail?.content_type === 'text/markdown'
      ? 'flex-1 min-h-0 overflow-y-auto'
      : 'flex-1 min-h-0 overflow-hidden'
    : 'flex-1 min-h-0 overflow-y-auto px-4 py-3';

  const handleArtifactDownload = useCallback(async () => {
    if (detail == null || !versionContentReady) return;

    setDownloadLoading(true);
    try {
      if (detail.has_blob) {
        const url = await api.fetchAdminArtifactRawObjectUrl(
          conversationId,
          detail.id,
        );
        triggerObjectUrlDownload(detail.original_filename ?? detail.title, url);
        return;
      }

      const filename = getTextArtifactDownloadFilename(
        detail.title,
        detail.content_type,
      );
      triggerBlobDownload(
        filename,
        new Blob([displayedContent], {
          type: `${detail.content_type};charset=utf-8`,
        }),
      );
    } catch (error) {
      const message = error instanceof Error
        ? error.message
        : '下载失败，请稍后重试';
      window.alert(message);
    } finally {
      setDownloadLoading(false);
    }
  }, [conversationId, detail, displayedContent, versionContentReady]);

  return (
    <div className="flex-1 flex min-h-0">
      <div className="w-[280px] flex-shrink-0 border-r border-border dark:border-border-dark overflow-y-auto">
        {listLoading ? (
          <div className="p-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">加载中…</div>
        ) : list == null || list.length === 0 ? (
          <div className="p-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">该会话暂无 artifacts</div>
        ) : (
          <div className="py-1">
            {list.map((artifact) => (
              <button
                key={artifact.id}
                onClick={() => setSelectedId(artifact.id)}
                className={`w-full text-left px-3 py-2 transition-colors ${
                  selectedId === artifact.id
                    ? 'bg-accent/10'
                    : 'hover:bg-surface dark:hover:bg-bg-dark'
                }`}
              >
                <div className="text-xs font-medium text-text-primary dark:text-text-primary-dark truncate">
                  {artifact.title}
                </div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-text-tertiary dark:text-text-tertiary-dark">
                  <span className="font-mono">{artifact.content_type}</span>
                  <span>v{artifact.current_version}</span>
                  {artifact.source ? <span>· {artifact.source}</span> : null}
                </div>
                <div className="mt-0.5 text-[10px] text-text-tertiary dark:text-text-tertiary-dark truncate">
                  {parseUtcIso(artifact.updated_at).toLocaleString('zh-CN')}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        {selectedId == null ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            从左侧选择一个 artifact 查看内容
          </div>
        ) : detailLoading ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载中…
          </div>
        ) : detail == null ? (
          <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载失败
          </div>
        ) : (
          <>
            <div className="px-4 pt-3 pb-2 border-b border-border dark:border-border-dark">
              <div className="text-sm font-semibold text-text-primary dark:text-text-primary-dark truncate">
                {detail.title}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[11px] text-text-tertiary dark:text-text-tertiary-dark flex-wrap">
                <span className="font-mono">{detail.content_type}</span>
                <span>·</span>
                <span>ID: {detail.id}</span>
                {detail.source ? <><span>·</span><span>{detail.source}</span></> : null}
                {detail.original_filename ? <><span>·</span><span>{detail.original_filename}</span></> : null}
                {detail.versions.length > 0 ? (
                  <>
                    <span>·</span>
                    <span className="relative">
                      <select
                        value={viewingVersion ?? detail.current_version}
                        onChange={(event) => setViewingVersion(Number(event.target.value))}
                        className={SELECT_COMPACT}
                      >
                        {detail.versions.map((version) => (
                          <option key={version.version} value={version.version}>
                            v{version.version} ({version.update_type})
                            {version.version === detail.current_version ? ' · current' : ''}
                          </option>
                        ))}
                      </select>
                      {SELECT_CHEVRON_COMPACT}
                    </span>
                    {versionLoading ? <span>加载…</span> : null}
                  </>
                ) : null}
                <button
                  type="button"
                  onClick={handleArtifactDownload}
                  disabled={downloadLoading || !versionContentReady}
                  className={`${BUTTON_SECONDARY} inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px]`}
                  aria-label="下载 artifact"
                  title="下载当前查看的版本"
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 14 14"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    aria-hidden="true"
                  >
                    <path d="M7 2v7.5M4 7l3 3 3-3M2.5 11.5h9" />
                  </svg>
                  {downloadLoading ? '下载中…' : '下载'}
                </button>
              </div>
            </div>

            <div className={contentClassName}>
              {versionContentReady ? (
                showingRichPreview ? (
                  <ArtifactPreviewContent
                    sessionId={conversationId}
                    artifactId={detail.id}
                    content={displayedContent}
                    contentType={detail.content_type}
                    hasBlob={!!detail.has_blob}
                    originalFilename={detail.original_filename}
                    refreshKey={detail.updated_at}
                    fetchRawBlob={api.fetchAdminArtifactRawBlob}
                    fetchRawObjectUrl={api.fetchAdminArtifactRawObjectUrl}
                    pendingFlush={false}
                    useLocalPreview={false}
                    showUnsupportedBinaryDownload={false}
                  />
                ) : (
                  <pre className="text-xs text-text-primary dark:text-text-primary-dark whitespace-pre-wrap break-words font-mono">
                    {displayedContent}
                  </pre>
                )
              ) : (
                <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
                  加载版本内容中…
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
