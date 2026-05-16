'use client';

import { memo, useState, useEffect, type ReactNode } from 'react';

interface DisclosureRowProps {
  /**
   * 'card' (default): bordered + rounded outer + `border-t` body divider — the
   *   original visual of ThinkingBlock / AgentOutputBlock / ToolCallCard.
   * 'inline': borderless, no body divider, no horizontal header padding. Used
   *   inside a parent card where the disclosure row is a list-style entry, not
   *   its own card. Callers control body padding/indent via `bodyClassName`.
   */
  variant?: 'card' | 'inline';
  /**
   * Content shown on the header before the label (e.g. status icon).
   * Rendered after the leading chevron when `chevronPosition === 'leading'`.
   */
  leading?: ReactNode;
  /** Main header text / inline content (always present). */
  label: ReactNode;
  /**
   * Right-aligned header content (e.g. duration). Pushed to the right edge via
   * `ml-auto`; when `chevronPosition === 'trailing'` the chevron sits after it.
   */
  trailing?: ReactNode;
  /** Where the chevron sits relative to leading/label/trailing. Defaults to 'leading'. */
  chevronPosition?: 'leading' | 'trailing';

  /** Initial expanded state. */
  defaultExpanded?: boolean;
  /**
   * When provided, the row auto-expands while truthy and auto-collapses on transition
   * to false. Pass `undefined` (omit) for callers that don't have a "live" notion —
   * e.g. ToolCallCard — so `defaultExpanded` is honored without override.
   */
  isLive?: boolean;

  /** Extra classes on the header button (e.g. text color overrides). */
  headerClassName?: string;
  /** Extra classes on the body wrapper. Caller controls padding/styling of body content. */
  bodyClassName?: string;

  /** Body content; when omitted the row is non-expandable and renders no chevron. */
  children?: ReactNode;
}

/**
 * Shared collapsible row primitive for in-segment blocks (Thinking, Agent Output, Tool Call).
 * Two variants share the same slot API (leading/label/trailing + chevron position):
 *   - 'card' (default) wraps the row in a bordered card — original visual.
 *   - 'inline' renders a borderless list-style row meant to live inside a parent card.
 */
function DisclosureRow({
  variant = 'card',
  leading,
  label,
  trailing,
  chevronPosition = 'leading',
  defaultExpanded = false,
  isLive,
  headerClassName,
  bodyClassName,
  children,
}: DisclosureRowProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  // Only react to isLive transitions when the caller opts in. Without this guard the
  // effect would force `expanded = false` on mount for non-live callers and clobber
  // `defaultExpanded`.
  useEffect(() => {
    if (isLive === undefined) return;
    setExpanded(isLive);
  }, [isLive]);

  const hasBody = children !== undefined && children !== null && children !== false;
  const isExpanded = expanded && hasBody;

  const chevron = hasBody ? (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      className={`flex-shrink-0 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
    >
      <path d="M4.5 2.5 8 6l-3.5 3.5" />
    </svg>
  ) : null;

  const isInline = variant === 'inline';
  const outerClass = isInline
    ? ''
    : 'border border-border dark:border-border-dark rounded-card overflow-hidden';
  const headerPaddingClass = isInline ? 'py-1.5' : 'px-3 py-2';
  // Inline rows have no outer rounded container to clip the hover background,
  // so the button itself needs to round.
  const headerRoundedClass = isInline ? 'rounded-md' : '';
  const bodyDividerClass = isInline ? '' : 'border-t border-border dark:border-border-dark';

  return (
    <div className={outerClass}>
      <button
        type="button"
        onClick={() => { if (hasBody) setExpanded(!expanded); }}
        className={`w-full flex items-center gap-2 ${headerPaddingClass} ${headerRoundedClass} text-xs transition-colors ${
          hasBody ? 'hover:bg-surface/60 dark:hover:bg-panel-accent-dark/60 cursor-pointer' : 'cursor-default'
        } ${headerClassName ?? ''}`}
      >
        {chevronPosition === 'leading' && chevron}
        {leading}
        {label}
        {trailing !== undefined && (
          <span className="ml-auto">{trailing}</span>
        )}
        {chevronPosition === 'trailing' && chevron}
      </button>

      {isExpanded && (
        <div className={`${bodyDividerClass} ${bodyClassName ?? ''}`}>
          {children}
        </div>
      )}
    </div>
  );
}

export default memo(DisclosureRow);
