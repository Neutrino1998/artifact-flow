'use client';

import { useEffect, useMemo, useState } from 'react';
import type { MessageFeedbackResponse } from '@/types';
import {
  BUTTON_DANGER,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  INPUT_ON_SURFACE,
} from '@/lib/styles';
import {
  FEEDBACK_TAG_LABELS,
  feedbackTagsFor,
  type FeedbackRating,
  type FeedbackTag,
} from '@/lib/messageFeedback';
import DialogShell from '@/components/layout/DialogShell';
import { useConfigStore } from '@/stores/configStore';

export default function MessageFeedbackDialog({
  rating,
  current,
  saving,
  error,
  onSubmit,
  onDelete,
  onClose,
}: {
  rating: FeedbackRating;
  current: MessageFeedbackResponse | null;
  saving: boolean;
  error: string | null;
  onSubmit: (tags: FeedbackTag[], detail: string) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const sameRating = current?.rating === rating;
  const maxDetailChars = useConfigStore((state) => state.messageFeedbackMaxDetailChars);
  const [tags, setTags] = useState<FeedbackTag[]>(
    sameRating && current ? current.tags : [],
  );
  const [detail, setDetail] = useState(
    sameRating && current ? (current.detail ?? '') : '',
  );
  const availableTags = useMemo(() => feedbackTagsFor(rating), [rating]);

  useEffect(() => {
    if (!current || current.rating !== rating) return;
    setTags(current.tags);
    setDetail(current.detail ?? '');
  }, [current, rating]);

  const toggleTag = (tag: FeedbackTag) => {
    setTags((prev) => prev.includes(tag)
      ? prev.filter((item) => item !== tag)
      : [...prev, tag]);
  };
  const guardedClose = () => { if (!saving) onClose(); };

  return (
    <DialogShell
      title="提交反馈"
      size="lg"
      onClose={guardedClose}
      closeOnBackdrop={!saving}
      closeOnEscape={!saving}
      surfaceClassName="bg-surface dark:bg-surface-dark"
      footer={
        <div className="flex w-full flex-col-reverse gap-2 sm:flex-row sm:items-center">
          {current ? (
            <button
              type="button"
              onClick={onDelete}
              disabled={saving}
              className={`${BUTTON_DANGER} rounded-lg px-5 py-2 sm:mr-auto disabled:opacity-50`}
            >
              撤销反馈
            </button>
          ) : null}
          <button
            type="button"
            onClick={guardedClose}
            disabled={saving}
            className={`${BUTTON_SECONDARY} rounded-lg px-6 py-2 disabled:opacity-50`}
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => onSubmit(tags, detail)}
            disabled={saving}
            className={`${BUTTON_PRIMARY} rounded-lg px-8 py-2 disabled:opacity-50`}
          >
            {saving ? '提交中…' : '提交'}
          </button>
        </div>
      }
    >
      <div className="mt-5 space-y-4">
        <div className="flex flex-wrap gap-2">
          {availableTags.map((tag) => {
            const selected = tags.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                aria-pressed={selected}
                onClick={() => toggleTag(tag)}
                disabled={saving}
                className={`rounded-full border px-4 py-2 text-sm transition-colors disabled:opacity-50 ${
                  selected
                    ? 'border-accent bg-accent/10 text-accent'
                    : 'border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark'
                }`}
              >
                {selected ? '✓' : '+'} {FEEDBACK_TAG_LABELS[tag]}
              </button>
            );
          })}
        </div>

        <div className="space-y-1 pb-3">
          <textarea
            value={detail}
            onChange={(event) => setDetail(event.target.value)}
            maxLength={maxDetailChars ?? undefined}
            disabled={saving}
            placeholder="填写详情（选填）"
            className={`${INPUT_ON_SURFACE} min-h-40 resize-y text-sm`}
          />
          <div className="flex items-center justify-between gap-4 text-xs text-text-tertiary dark:text-text-tertiary-dark">
            <span>反馈仅保存在当前私有部署中，供管理员排查和改进服务。</span>
            <span className="shrink-0">
              {detail.length}{maxDetailChars != null ? `/${maxDetailChars}` : ''}
            </span>
          </div>
        </div>
        {error ? <div className="text-sm text-status-error">{error}</div> : null}
      </div>
    </DialogShell>
  );
}
