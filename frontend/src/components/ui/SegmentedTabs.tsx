'use client';

import type { ReactNode } from 'react';

export type SegmentedTabOption<T extends string> = {
  value: T;
  label: ReactNode;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
};

export function SegmentedTabs<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className = '',
}: {
  value: T;
  options: readonly SegmentedTabOption<T>[];
  onChange: (value: T) => void;
  ariaLabel?: string;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`inline-flex p-0.5 rounded-lg bg-panel-accent dark:bg-surface-dark text-xs ${className}`}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={option.ariaLabel}
            title={option.title}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            className={`px-3 py-1 rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              active
                ? 'bg-surface dark:bg-bg-dark text-accent font-medium shadow-sm'
                : 'text-text-tertiary dark:text-text-tertiary-dark hover:text-text-secondary dark:hover:text-text-secondary-dark'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
