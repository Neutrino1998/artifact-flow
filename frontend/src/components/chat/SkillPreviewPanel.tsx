'use client';

import { useEffect, useState } from 'react';
import { getSkillDetail } from '@/lib/api';
import { useUIStore } from '@/stores/uiStore';
import { useLatestOnly } from '@/hooks/useLatestOnly';
import { useCopyFeedback } from '@/hooks/useCopyFeedback';
import { BUTTON_GHOST_ICON } from '@/lib/styles';
import { PillBadge } from '@/components/ui/PillBadge';
import MarkdownPreview from '@/components/artifact/MarkdownPreview';
import type { SkillDetailResponse } from '@/types';

export default function SkillPreviewPanel() {
  const view = useUIStore((s) => s.skillRightView);
  const setArtifactPanelVisible = useUIStore((s) => s.setArtifactPanelVisible);
  const [detail, setDetail] = useState<SkillDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const claim = useLatestOnly();
  const { copied, copy } = useCopyFeedback();

  useEffect(() => {
    const isLatest = claim();
    if (view.type !== 'detail') {
      setDetail(null);
      setError(null);
      setLoading(false);
      return;
    }

    setDetail(null);
    setError(null);
    setLoading(true);
    let active = true;
    getSkillDetail(view.skillId, { admin: view.admin })
      .then((result) => {
        if (active && isLatest()) setDetail(result);
      })
      .catch((err) => {
        if (active && isLatest()) {
          setError(err instanceof Error ? err.message : '加载技能说明失败');
        }
      })
      .finally(() => {
        if (active && isLatest()) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [claim, view]);

  if (view.type === 'empty') {
    return (
      <div className="flex h-full items-center justify-center bg-chat p-6 text-center text-sm text-text-tertiary dark:bg-chat-dark dark:text-text-tertiary-dark">
        选择一个技能查看 SKILL.md 说明
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-chat dark:bg-chat-dark">
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3 dark:border-border-dark">
        <div className="min-w-0">
          <h2 className="break-words text-sm font-semibold text-text-primary dark:text-text-primary-dark">
            {detail?.name ?? '技能说明'}
          </h2>
          {detail && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs text-text-tertiary dark:text-text-tertiary-dark">
                {detail.slug}
              </span>
              <PillBadge tone={detail.source === 'dynamic' ? 'accent' : 'neutral'}>
                {detail.source === 'dynamic' ? '导入' : '内置'}
              </PillBadge>
              <PillBadge tone={detail.visibility === 'department' ? 'warning' : 'neutral'}>
                {detail.visibility === 'private'
                  ? '私有'
                  : detail.visibility === 'department'
                    ? '部门'
                    : '公开'}
              </PillBadge>
            </div>
          )}
        </div>

        <div className="flex flex-shrink-0 items-center gap-1">
          {detail && (
            <button
              type="button"
              onClick={() => copy(detail.skill_md)}
              className={`${BUTTON_GHOST_ICON} p-1.5`}
              aria-label="复制技能说明"
              title={copied ? '已复制' : '复制正文'}
            >
              {copied ? (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M3 7.5 6 10.5l5-7" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <rect x="4.5" y="4.5" width="7" height="7" rx="1" />
                  <path d="M9.5 4.5V3a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v5.5a1 1 0 0 0 1 1h1.5" />
                </svg>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => setArtifactPanelVisible(false)}
            className={`${BUTTON_GHOST_ICON} hidden p-1.5 md:flex`}
            aria-label="关闭技能说明"
            title="关闭"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M3 3l8 8M11 3l-8 8" />
            </svg>
          </button>
        </div>
      </div>

      {detail?.description && (
        <p className="border-b border-border px-4 py-2 text-xs text-text-secondary dark:border-border-dark dark:text-text-secondary-dark">
          {detail.description}
        </p>
      )}

      {detail?.has_extra_files && (
        <p className="border-b border-border bg-panel-accent px-4 py-2 text-xs text-text-secondary dark:border-border-dark dark:bg-panel-dark dark:text-text-secondary-dark">
          此技能还包含附属文件；这里仅预览 SKILL.md 正文，可从技能列表导出完整技能包。
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
            加载技能说明中...
          </div>
        ) : error ? (
          <div className="p-5 text-sm text-status-error">{error}</div>
        ) : detail ? (
          <MarkdownPreview content={detail.skill_md} />
        ) : null}
      </div>
    </div>
  );
}
