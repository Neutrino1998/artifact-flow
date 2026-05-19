'use client';

import type { ReactNode } from 'react';

interface PanelShellProps {
  /** Rendered inside the header chrome (`px-6 pt-5 pb-3 border-b ...`). Omit for header-less panels. */
  header?: ReactNode;
  /** Rendered inside the footer chrome (`px-6 py-4 flex justify-end gap-3`). Omit for footer-less panels. */
  footer?: ReactNode;
  /**
   * Body content. Caller controls padding / scrolling because variation here
   * is real — forms use `flex-1 overflow-y-auto px-6 py-5 space-y-N`, the
   * department-manager has a non-scrolling toolbar between header and a
   * scrollable list, BulkImportForm switches body structure per stage.
   */
  children?: ReactNode;
}

/**
 * Right-side management panel shell. Owns the outer container, the header
 * `border-b` chrome, and the footer padding chrome. Header inner layout
 * (title+close vs back-button) and body padding stay at the call site.
 */
export default function PanelShell({ header, footer, children }: PanelShellProps) {
  return (
    <div className="h-full flex flex-col min-h-0 bg-chat dark:bg-chat-dark">
      {header && (
        <div className="px-6 pt-5 pb-3 border-b border-border dark:border-border-dark">
          {header}
        </div>
      )}
      {children}
      {footer && (
        <div className="px-6 py-4 flex justify-end gap-3">
          {footer}
        </div>
      )}
    </div>
  );
}
