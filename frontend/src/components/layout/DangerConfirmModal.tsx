'use client';

import { useEffect, useState } from 'react';
import { ApiError } from '@/lib/api';

interface DangerConfirmModalProps {
  title: string;
  /** 主体说明（可包含影响数据，如 "将级联删除该用户的 N 条会话"） */
  message: string;
  /** checkbox 必勾才允许确认 — 防误触 */
  acknowledgeLabel?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /**
   * 确认时的 async handler；执行期间按钮显示 loading。
   * 抛错时由本 modal 接住并 inline 显示，modal 保持打开供用户重试或取消，
   * 避免 caller 把删除失败变成 unhandled rejection。
   */
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export default function DangerConfirmModal({
  title,
  message,
  acknowledgeLabel = '我已了解此操作不可恢复',
  confirmLabel = '确认删除',
  cancelLabel = '取消',
  onConfirm,
  onCancel,
}: DangerConfirmModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ESC 关闭（提交中除外）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onCancel();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onCancel, submitting]);

  const handleConfirm = async () => {
    if (!acknowledged || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      // 失败时不关闭 modal — 用户可重试或取消，不至于静默失败
      if (err instanceof ApiError) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message || '操作失败');
      } else {
        setError('操作失败');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={() => !submitting && onCancel()}
    >
      <div
        className="bg-surface dark:bg-surface-dark border border-border dark:border-border-dark rounded-card shadow-modal max-w-md w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-2">
          {title}
        </h2>
        <p className="text-text-secondary dark:text-text-secondary-dark whitespace-pre-line mb-6">
          {message}
        </p>

        <label className="flex items-start gap-3 mb-4 cursor-pointer select-none group">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            disabled={submitting}
            className="mt-0.5 w-4 h-4 accent-status-error cursor-pointer disabled:cursor-not-allowed"
          />
          <span className="text-sm text-text-secondary dark:text-text-secondary-dark group-hover:text-text-primary dark:group-hover:text-text-primary-dark transition-colors">
            {acknowledgeLabel}
          </span>
        </label>

        {error && (
          <div
            role="alert"
            className="mb-4 px-3 py-2 text-sm text-status-error bg-status-error/10 border border-status-error/30 rounded-lg"
          >
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="px-8 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            onClick={handleConfirm}
            disabled={!acknowledged || submitting}
            className="px-8 py-2 rounded-lg text-white bg-status-error hover:bg-status-error/80 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? '处理中...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
