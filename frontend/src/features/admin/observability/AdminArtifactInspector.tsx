'use client';

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import ArtifactPreviewContent from '@/components/artifact/ArtifactPreviewContent';
import {
  ArtifactBrowserIcon,
  ArtifactFileIcon,
  artifactFileTypeLabel,
} from '@/components/artifact/ArtifactFileIcon';
import ArtifactTree from '@/components/artifact/ArtifactTree';
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
import type { AdminArtifactSummary } from '@/lib/api';
import type { ArtifactDetail, VersionDetail } from '@/types';

function formatDateTime(value: string): string {
  try {
    return parseUtcIso(value).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return value;
  }
}

function MetadataItem({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={`min-w-0 ${wide ? 'sm:col-span-2' : ''}`}>
      <dt className="text-[10px] uppercase tracking-wide text-text-tertiary dark:text-text-tertiary-dark">
        {label}
      </dt>
      <dd className="mt-0.5 break-words text-xs text-text-secondary dark:text-text-secondary-dark">
        {children}
      </dd>
    </div>
  );
}

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
 * change the current user's artifact selection or live SSE state. Presentation
 * primitives are shared, while selection and fetching remain admin-local.
 */
export default function AdminArtifactInspector({
  conversationId,
  refreshTick,
}: {
  conversationId: string;
  refreshTick: number;
}) {
  const [list, setList] = useState<AdminArtifactSummary[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [viewingVersion, setViewingVersion] = useState<number | null>(null);
  const [versionContent, setVersionContent] = useState<VersionDetail | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const [downloadLoading, setDownloadLoading] = useState(false);

  const protectedUploadCount = list?.filter(
    (artifact) => artifact.content_accessible === false,
  ).length ?? 0;
  const accessibleArtifacts = list?.filter(
    (artifact) => artifact.content_accessible !== false,
  ) ?? [];

  useEffect(() => {
    setList(null);
    setSelectedId(null);
    setDetail(null);
    setViewingVersion(null);
    setVersionContent(null);
    setListError(false);
    let cancelled = false;
    setListLoading(true);
    api.listAdminConversationArtifacts(conversationId).then((response) => {
      if (!cancelled) setList(response.artifacts);
    }).catch((error) => {
      if (!cancelled) {
        console.error('Failed to load artifacts:', error);
        setList([]);
        setListError(true);
      }
    }).finally(() => {
      if (!cancelled) setListLoading(false);
    });
    return () => { cancelled = true; };
  }, [conversationId, refreshTick]);

  useEffect(() => {
    if (selectedId == null) {
      setDetail(null);
      setDetailError(false);
      setViewingVersion(null);
      setVersionContent(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(false);
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
        setDetailError(true);
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

  if (selectedId == null) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        {protectedUploadCount > 0 ? (
          <div className="mx-3 mt-3 shrink-0 rounded-lg bg-panel-accent px-3 py-2 text-xs text-text-secondary dark:bg-surface-dark dark:text-text-secondary-dark">
            {protectedUploadCount} 个用户上传文件受隐私保护，管理员不能预览或下载。
          </div>
        ) : null}
        <ArtifactTree
          artifacts={accessibleArtifacts}
          loading={listLoading || list == null}
          onSelect={setSelectedId}
          heading="会话文件"
          emptyMessage={listError
            ? '文件列表加载失败'
            : protectedUploadCount > 0
              ? '没有可查看的会话文件'
              : '该会话暂无文件'}
          showTypeLabel={false}
          showMetadataTooltip={false}
          idPrefix="admin-artifact-source"
        />
      </div>
    );
  }

  if (detailLoading || (detail == null && !detailError)) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-text-tertiary dark:text-text-tertiary-dark">
        加载中…
      </div>
    );
  }

  if (detail == null) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-xs text-text-tertiary dark:text-text-tertiary-dark">
        <span>文件加载失败</span>
        <button
          type="button"
          onClick={() => setSelectedId(null)}
          className={`${BUTTON_SECONDARY} inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs`}
        >
          <ArtifactBrowserIcon />
          返回文件列表
        </button>
      </div>
    );
  }

  const displayName = detail.original_filename || detail.title;
  const fileType = artifactFileTypeLabel(detail.content_type, displayName);
  const selectedVersionSummary = detail.versions.find(
    (version) => version.version === viewingVersion,
  );

  return (
    <div className="flex-1 flex min-h-0 flex-col min-w-0">
      <div className="shrink-0 border-b border-border px-4 py-3 dark:border-border-dark">
        <div className="flex flex-wrap items-start gap-3">
          <ArtifactFileIcon contentType={detail.content_type} filename={displayName} />
          <div className="min-w-0 flex-1">
            <h3 className="break-words text-sm font-semibold leading-5 text-text-primary dark:text-text-primary-dark">
              {displayName}
            </h3>
            {detail.original_filename && detail.title !== detail.original_filename ? (
              <div className="mt-0.5 truncate text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
                Artifact 标题：{detail.title}
              </div>
            ) : null}
          </div>

          <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
            {detail.versions.length > 0 ? (
              <span className="relative">
                <select
                  value={viewingVersion ?? detail.current_version}
                  onChange={(event) => setViewingVersion(Number(event.target.value))}
                  className={SELECT_COMPACT}
                  aria-label="选择文件版本"
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
            ) : null}
            {versionLoading ? (
              <span className="px-1 text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
                加载…
              </span>
            ) : null}
            <button
              type="button"
              onClick={handleArtifactDownload}
              disabled={downloadLoading || !versionContentReady}
              className={`${BUTTON_SECONDARY} inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px]`}
              aria-label="下载文件"
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
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className={`${BUTTON_SECONDARY} inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px]`}
              aria-label="返回文件列表"
              title="返回文件列表"
            >
              <ArtifactBrowserIcon />
              文件列表
            </button>
          </div>
        </div>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 rounded-lg bg-panel-accent px-3 py-2.5 dark:bg-surface-dark md:grid-cols-4">
          <MetadataItem label="文件类型">
            <span className="font-medium">{fileType}</span>
            <span className="ml-1.5 font-mono text-[11px] text-text-tertiary dark:text-text-tertiary-dark">
              {detail.content_type}
            </span>
          </MetadataItem>
          <MetadataItem label="来源">{detail.source || 'other'}</MetadataItem>
          <MetadataItem label="创建时间">{formatDateTime(detail.created_at)}</MetadataItem>
          <MetadataItem label="最后更新">{formatDateTime(detail.updated_at)}</MetadataItem>
          {detail.original_filename ? (
            <MetadataItem label="原始文件名" wide>{detail.original_filename}</MetadataItem>
          ) : null}
          <MetadataItem label="Artifact ID" wide>
            <span className="font-mono text-[11px]">{detail.id}</span>
          </MetadataItem>
          <MetadataItem label="当前查看版本">
            v{viewingVersion ?? detail.current_version}
            {selectedVersionSummary ? ` · ${selectedVersionSummary.update_type}` : ''}
          </MetadataItem>
          <MetadataItem label="版本创建时间">
            {selectedVersionSummary ? formatDateTime(selectedVersionSummary.created_at) : '—'}
          </MetadataItem>
        </dl>
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
            <pre className="whitespace-pre-wrap break-words font-mono text-xs text-text-primary dark:text-text-primary-dark">
              {displayedContent}
            </pre>
          )
        ) : (
          <div className="text-xs text-text-tertiary dark:text-text-tertiary-dark">
            加载版本内容中…
          </div>
        )}
      </div>
    </div>
  );
}
