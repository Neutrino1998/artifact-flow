// Shared chevron overlay for appearance-none <select> elements.
// Usage: wrap the select in a `relative` container, compose the select's
// className with ' appearance-none pr-9' (or SELECT_COMPACT which has pr-7),
// and render {SELECT_CHEVRON} as a sibling.
export const SELECT_CHEVRON = (
  <svg
    className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
    width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
  >
    <path d="M3 4.5l3 3 3-3" />
  </svg>
);

// Compact variant for SELECT_COMPACT (tighter right offset to match pr-7).
export const SELECT_CHEVRON_COMPACT = (
  <svg
    className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-tertiary dark:text-text-tertiary-dark"
    width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
  >
    <path d="M3 4.5l3 3 3-3" />
  </svg>
);
