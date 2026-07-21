'use client';

import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';

interface FullscreenViewerProps {
  open: boolean;
  title: string;
  onClose: () => void;
  toolbarActions?: ReactNode;
  children: ReactNode;
}

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

/**
 * Shared immersive viewer shell. It owns only modal mechanics (portal, focus,
 * Escape, scroll lock and the chrome); callers own the viewed content and its
 * data lifecycle. Visual media normally supplies a ZoomableCanvas as children.
 */
export default function FullscreenViewer({
  open,
  title,
  onClose,
  toolbarActions,
  children,
}: FullscreenViewerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = `fullscreen-viewer-${useId().replace(/[:]/g, '')}`;

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab' || !rootRef.current) return;

      const focusable = Array.from(
        rootRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((element) => element.getClientRects().length > 0 || element === document.activeElement);
      if (focusable.length === 0) {
        event.preventDefault();
        rootRef.current.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  return createPortal(
    <div
      ref={rootRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="fixed inset-0 z-[70] overflow-hidden bg-chat/95 dark:bg-black/90 backdrop-blur-sm"
    >
      <h2
        id={titleId}
        className="absolute left-4 top-4 z-20 max-w-[calc(100vw-11rem)] truncate rounded-full border border-border/70 dark:border-border-dark bg-surface/90 dark:bg-surface-dark/90 px-4 py-2 text-sm font-medium text-text-primary dark:text-text-primary-dark shadow-float"
      >
        {title}
      </h2>

      <div className="absolute right-4 top-4 z-20 flex items-center gap-2">
        {toolbarActions}
        <ViewerToolbarButton
          ref={closeButtonRef}
          onClick={onClose}
          aria-label="Close fullscreen viewer"
          title="关闭"
        >
          <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </ViewerToolbarButton>
      </div>

      <div className="absolute inset-0">{children}</div>
    </div>,
    document.body,
  );
}

export const ViewerToolbarButton = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement>
>(function ViewerToolbarButton({ className = '', ...props }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className={`flex h-10 w-10 items-center justify-center rounded-full border border-border/70 dark:border-border-dark bg-surface/90 dark:bg-surface-dark/90 text-text-secondary dark:text-text-secondary-dark shadow-float hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-surface dark:hover:bg-surface-dark disabled:cursor-not-allowed disabled:opacity-40 transition-colors ${className}`}
      {...props}
    />
  );
});
