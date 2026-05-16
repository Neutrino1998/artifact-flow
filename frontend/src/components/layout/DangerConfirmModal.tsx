'use client';

import { useState } from 'react';
import { ApiError } from '@/lib/api';
import { BUTTON_DANGER, BUTTON_SECONDARY } from '@/lib/styles';
import DialogShell from './DialogShell';

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
    <DialogShell
      title={title}
      description={<span className="whitespace-pre-line">{message}</span>}
      size="md"
      onClose={onCancel}
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
      footer={
        <>
          <button
            onClick={onCancel}
            disabled={submitting}
            className={`${BUTTON_SECONDARY} rounded-lg px-8 py-2`}
          >
            {cancelLabel}
          </button>
          <button
            onClick={handleConfirm}
            disabled={!acknowledged || submitting}
            className={`${BUTTON_DANGER} rounded-lg px-8 py-2`}
          >
            {submitting ? '处理中...' : confirmLabel}
          </button>
        </>
      }
    >
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
    </DialogShell>
  );
}
