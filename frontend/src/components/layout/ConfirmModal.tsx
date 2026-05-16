'use client';

import { BUTTON_DANGER, BUTTON_PRIMARY, BUTTON_SECONDARY } from '@/lib/styles';
import DialogShell from './DialogShell';

interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  return (
    <DialogShell
      title={title}
      description={message}
      onClose={onCancel}
      footer={
        <>
          <button
            onClick={onCancel}
            className={`${BUTTON_SECONDARY} rounded-lg px-8 py-2`}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            className={`${destructive ? BUTTON_DANGER : BUTTON_PRIMARY} rounded-lg px-8 py-2`}
          >
            {confirmLabel}
          </button>
        </>
      }
    />
  );
}
