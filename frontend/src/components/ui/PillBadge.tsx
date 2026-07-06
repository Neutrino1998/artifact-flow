'use client';

import type { ReactNode } from 'react';

type PillBadgeTone = 'accent' | 'neutral' | 'success' | 'warning' | 'error';
type PillBadgeSize = 'compact' | 'regular';

const TONE_CLASS: Record<PillBadgeTone, string> = {
  accent: 'bg-accent/10 text-accent',
  neutral: 'bg-bg dark:bg-bg-dark text-text-secondary dark:text-text-secondary-dark',
  success: 'bg-status-success/10 text-status-success',
  warning: 'bg-status-warning/10 text-status-warning',
  error: 'bg-status-error/10 text-status-error',
};

const SIZE_CLASS: Record<PillBadgeSize, string> = {
  compact: 'px-1.5 py-px text-[10px]',
  regular: 'px-2 py-0.5 text-xs',
};

export function PillBadge({
  children,
  tone = 'neutral',
  size = 'compact',
  className = '',
  title,
}: {
  children: ReactNode;
  tone?: PillBadgeTone;
  size?: PillBadgeSize;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded-full font-medium shrink-0 whitespace-nowrap ${SIZE_CLASS[size]} ${TONE_CLASS[tone]} ${className}`}
    >
      {children}
    </span>
  );
}
