'use client';

import type { ReactNode } from 'react';

type StatusNoticeTone = 'success' | 'error' | 'warning' | 'accent';

const TONE_CLASS: Record<StatusNoticeTone, { root: string; icon: string; hover: string }> = {
  success: {
    root: 'border-status-success/30 bg-status-success/10',
    icon: 'bg-status-success text-white',
    hover: 'hover:bg-status-success/10',
  },
  error: {
    root: 'border-status-error/30 bg-status-error/10',
    icon: 'bg-status-error text-white',
    hover: 'hover:bg-status-error/10',
  },
  warning: {
    root: 'border-status-warning/30 bg-status-warning/10',
    icon: 'bg-status-warning text-white',
    hover: 'hover:bg-status-warning/10',
  },
  accent: {
    root: 'border-accent/30 bg-accent/10',
    icon: 'bg-accent text-white',
    hover: 'hover:bg-accent/10',
  },
};

function StatusIcon({ tone }: { tone: StatusNoticeTone }) {
  if (tone === 'success') {
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3.5 8.5l3 3 6-7" />
      </svg>
    );
  }

  if (tone === 'error') {
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
      </svg>
    );
  }

  if (tone === 'warning') {
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 2.25v5.75" strokeWidth="2.25" />
        <circle cx="8" cy="12.25" r="1.5" fill="currentColor" stroke="none" />
      </svg>
    );
  }

  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="8" cy="8" r="6" />
      <path d="M8 7.5v4M8 4.5h.01" />
    </svg>
  );
}

export function StatusNotice({
  tone = 'accent',
  title,
  children,
  actions,
  onDismiss,
  dismissLabel = '关闭提示',
  role,
  className = '',
}: {
  tone?: StatusNoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  onDismiss?: () => void;
  dismissLabel?: string;
  role?: 'status' | 'alert';
  className?: string;
}) {
  const toneClass = TONE_CLASS[tone];

  return (
    <div
      role={role ?? (tone === 'error' ? 'alert' : 'status')}
      className={`rounded-xl border px-3 py-3 text-sm text-text-secondary dark:text-text-secondary-dark ${toneClass.root} ${className}`}
    >
      <div className="flex items-start gap-3">
        <span className={`mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full ${toneClass.icon}`}>
          <StatusIcon tone={tone} />
        </span>
        <div className="min-w-0 flex-1">
          {title && (
            <div className="flex flex-wrap items-center gap-2 font-medium text-text-primary dark:text-text-primary-dark">
              {title}
            </div>
          )}
          {children && (
            <div className={title ? 'mt-2' : undefined}>
              {children}
            </div>
          )}
        </div>
        {(actions || onDismiss) && (
          <div className="flex flex-shrink-0 items-center gap-1">
            {actions}
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                className={`rounded-lg p-1 text-text-tertiary dark:text-text-tertiary-dark ${toneClass.hover} hover:text-text-secondary dark:hover:text-text-secondary-dark transition-colors`}
                aria-label={dismissLabel}
                title={dismissLabel}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                  <path d="M4 4l8 8M12 4l-8 8" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
