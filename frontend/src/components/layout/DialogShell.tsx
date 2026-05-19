'use client';

import { useEffect, type ReactNode } from 'react';

interface DialogShellProps {
  /** Title rendered as <h2>. Required so every dialog has a clear heading. */
  title: string;
  /** Optional secondary copy under the title. */
  description?: ReactNode;
  /** Body content (form, checkbox, etc.) between description and footer. */
  children?: ReactNode;
  /** Footer slot — typically a <DialogFooter> with action buttons. */
  footer?: ReactNode;
  /** Width tier. `sm` ≈ 384px, `md` ≈ 448px (matches existing usage). */
  size?: 'sm' | 'md';
  /** Called when the user requests close (backdrop click / ESC). */
  onClose: () => void;
  /** Backdrop click closes the dialog when true (default true). */
  closeOnBackdrop?: boolean;
  /** ESC key closes the dialog when true (default true). */
  closeOnEscape?: boolean;
  /** Override the surface bg/border (default `bg-surface dark:bg-surface-dark`). */
  surfaceClassName?: string;
}

/**
 * Modal shell: fixed overlay + centered surface + title/description + body
 * + optional footer slot. Owns ESC and backdrop-click behavior so individual
 * modals don't reimplement (and previously diverge on) these.
 *
 * Note: shell does NOT manage state — callers control mount/unmount and pass
 * their own loading / acknowledge / success states through `children`.
 */
export default function DialogShell({
  title,
  description,
  children,
  footer,
  size = 'sm',
  onClose,
  closeOnBackdrop = true,
  closeOnEscape = true,
  surfaceClassName = 'bg-surface dark:bg-surface-dark',
}: DialogShellProps) {
  useEffect(() => {
    if (!closeOnEscape) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [closeOnEscape, onClose]);

  const sizeClass = size === 'md' ? 'max-w-md' : 'max-w-sm';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      onClick={closeOnBackdrop ? onClose : undefined}
    >
      <div
        className={`${surfaceClassName} border border-border dark:border-border-dark rounded-card shadow-modal ${sizeClass} w-full mx-4 p-6`}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-text-primary dark:text-text-primary-dark mb-1">
          {title}
        </h2>
        {description && (
          <div className="text-text-secondary dark:text-text-secondary-dark mb-6 text-sm">
            {description}
          </div>
        )}
        {children}
        {footer && (
          <div className="flex justify-end gap-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
