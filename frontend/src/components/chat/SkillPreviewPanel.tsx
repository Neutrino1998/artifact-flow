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

function buildPreviewContent(detail: SkillDetailResponse): string {
  const sections: string[] = [];

  if (detail.description || detail.has_extra_files) {
    sections.push('### Description');
    if (detail.description) sections.push(detail.description);
    if (detail.has_extra_files) {
      sections.push(
        '`此技能包含附属文件；当前仅预览 SKILL.md 正文，完整技能包可从列表导出。`',
      );
    }
    sections.push('---');
  }

  sections.push(detail.skill_md);
  return sections.join('\n\n');
}

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
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <h2 className="mr-0.5 break-words text-sm font-semibold text-text-primary dark:text-text-primary-dark">
            {detail?.name ?? '技能说明'}
          </h2>
          {detail && (
            <>
              <PillBadge
                tone={detail.source === 'dynamic' ? 'accent' : 'neutral'}
                title={detail.source === 'dynamic' ? '通过界面导入的技能' : '随系统提供的内置技能'}
              >
                {detail.source === 'dynamic' ? '导入' : '内置'}
              </PillBadge>
              <PillBadge
                tone={detail.visibility === 'department' ? 'warning' : 'neutral'}
                title={
                  detail.visibility === 'private'
                    ? '仅自己可见'
                    : detail.visibility === 'department'
                      ? '部门可见：默认不可用，需要部门授权'
                      : '公开可见：默认全员可用，可被部门排除'
                }
              >
                {detail.visibility === 'private'
                  ? '私有'
                  : detail.visibility === 'department'
                    ? '部门'
                    : '公开'}
              </PillBadge>
            </>
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

      <div className="min-h-0 flex-1 overflow-auto">
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-text-tertiary dark:text-text-tertiary-dark">
            加载技能说明中...
          </div>
        ) : error ? (
          <div className="p-5 text-sm text-status-error">{error}</div>
        ) : detail ? (
          <MarkdownPreview content={buildPreviewContent(detail)} />
        ) : null}
      </div>
    </div>
  );
}
