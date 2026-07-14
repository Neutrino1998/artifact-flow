'use client';

import { useState, type ReactNode } from 'react';
import { ApiError } from '@/lib/api';
import { BUTTON_DANGER, BUTTON_SECONDARY } from '@/lib/styles';
import Checkbox from '@/components/forms/Checkbox';
import InlineMarkdown from '@/components/markdown/InlineMarkdown';
import { StatusNotice } from '@/components/ui/StatusNotice';
import DialogShell from './DialogShell';

const IRREVERSIBLE_MESSAGE = '操作不可恢复。';

interface DangerConfirmModalProps {
  title: string;
  /** 主体说明；包含“操作不可恢复。”时会统一渲染为独立警示行。 */
  message: string;
  /**
   * 是否需要额外勾选确认闸。默认 false：危险操作已经有一次明确弹窗确认，
   * 只在确实需要强摩擦的场景 opt in。
   */
  requireAcknowledge?: boolean;
  /**
   * 可选 body 内容,渲染在勾选/错误之上 —— 给调用方放上下文信息卡（如删除目标的
   * name/description,对齐权限确认弹窗的凹槽信息卡)。不传则 body 只有勾选/错误。
   */
  children?: ReactNode;
  /** checkbox 必勾才允许确认 — 防误触 */
  acknowledgeLabel?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 由调用方控制的确认禁用态，例如影响数据还在加载。 */
  confirmDisabled?: boolean;
  /**
   * 确认时的 async handler；执行期间按钮显示 loading。
   * 抛错时由本 modal 接住并 inline 显示，modal 保持打开供用户重试或取消，
   * 避免 caller 把删除失败变成 unhandled rejection。
   */
  onConfirm: () => void | Promise<void>;
  onCancel: () => void;
}

export function DangerConfirmTarget({
  name,
  description,
}: {
  name: string;
  description?: string | null;
}) {
  return (
    <div className="rounded-lg border border-border dark:border-border-dark bg-surface dark:bg-surface-dark p-3">
      <div className="break-words text-sm font-semibold text-text-primary dark:text-text-primary-dark">
        {name}
      </div>
      {description && (
        <InlineMarkdown className="mt-1.5 text-sm leading-relaxed prose-p:leading-relaxed">
          {description}
        </InlineMarkdown>
      )}
    </div>
  );
}

function formatDangerMessage(message: string) {
  const lines: string[] = [];
  let hasIrreversibleMessage = false;

  for (const rawLine of message.split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;

    const markerRegex = /(?:此操作不可恢复。|操作不可恢复。)/g;
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = markerRegex.exec(line)) !== null) {
      const before = line.slice(lastIndex, match.index).replace(/[，,；;\s]+$/, '').trim();
      if (before) lines.push(before);
      hasIrreversibleMessage = true;
      lastIndex = match.index + match[0].length;
    }

    const after = line.slice(lastIndex).replace(/^[，,；;\s]+/, '').trim();
    if (after) lines.push(after);
  }

  return { lines, hasIrreversibleMessage };
}

export default function DangerConfirmModal({
  title,
  message,
  requireAcknowledge = false,
  children,
  acknowledgeLabel = '我已了解此操作不可恢复',
  confirmLabel = '确认删除',
  cancelLabel = '取消',
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: DangerConfirmModalProps) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const blocked = confirmDisabled || (requireAcknowledge && !acknowledged) || submitting;
  const { lines, hasIrreversibleMessage } = formatDangerMessage(message);

  const handleConfirm = async () => {
    if (blocked) return;
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
      size="md"
      onClose={onCancel}
      closeOnBackdrop={!submitting}
      closeOnEscape={!submitting}
      surfaceClassName="bg-chat dark:bg-chat-dark"
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
            disabled={blocked}
            className={`${BUTTON_DANGER} rounded-lg px-8 py-2`}
          >
            {submitting ? '处理中…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="mt-4 mb-5 space-y-4">
        {children}

        <div className="space-y-2 text-sm leading-relaxed text-text-secondary dark:text-text-secondary-dark">
          {lines.map((line, index) => (
            <p key={`${line}-${index}`}>{line}</p>
          ))}
          {hasIrreversibleMessage && (
            <p className="font-medium text-status-error">{IRREVERSIBLE_MESSAGE}</p>
          )}
        </div>
      </div>

      {requireAcknowledge && (
        <label className="flex items-start gap-3 mb-4 cursor-pointer select-none group">
          <span className="mt-0.5 flex">
            <Checkbox
              variant="danger"
              checked={acknowledged}
              onChange={setAcknowledged}
              disabled={submitting}
              ariaLabel={acknowledgeLabel}
            />
          </span>
          <span className="text-sm text-text-secondary dark:text-text-secondary-dark group-hover:text-text-primary dark:group-hover:text-text-primary-dark transition-colors">
            {acknowledgeLabel}
          </span>
        </label>
      )}

      {error && (
        <StatusNotice tone="error" className="mb-4">
          {error}
        </StatusNotice>
      )}
    </DialogShell>
  );
}
