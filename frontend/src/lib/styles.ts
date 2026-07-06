export const PROSE_CLASSES =
  'prose prose-base dark:prose-invert max-w-none font-serif text-text-primary dark:text-text-primary-dark prose-headings:text-text-primary dark:prose-headings:text-text-primary-dark prose-strong:text-text-primary dark:prose-strong:text-text-primary-dark prose-a:text-accent prose-code:text-text-primary prose-code:before:content-none prose-code:after:content-none prose-code:bg-panel-accent prose-code:rounded prose-code:px-1 prose-code:py-0.5 prose-code:font-mono dark:prose-code:text-accent dark:prose-code:bg-bg-dark prose-pre:bg-panel-accent dark:prose-pre:bg-bg-dark prose-pre:text-text-primary dark:prose-pre:text-text-primary-dark prose-pre:border prose-pre:border-border dark:prose-pre:border-border-dark prose-pre:font-mono prose-hr:border-border dark:prose-hr:border-border-dark prose-thead:border-border dark:prose-thead:border-border-dark prose-tr:border-border dark:prose-tr:border-border-dark prose-th:border-border dark:prose-th:border-border-dark prose-td:border-border dark:prose-td:border-border-dark prose-blockquote:border-l-panel-accent dark:prose-blockquote:border-l-accent prose-li:marker:text-text-primary dark:prose-li:marker:text-text-primary-dark';

// ---------------------------------------------------------------------------
// Form primitives
//
// Inputs need to visually recede one shade from their parent surface (the
// "well" effect). The project has two parent surfaces that host forms, so we
// expose two variants — callers pick the one matching their parent. Picking
// the wrong one yields invisible inputs in dark mode, which is why we don't
// collapse this to a single INPUT_CLASS.
//
//   INPUT_ON_PANEL    — parent is bg-chat (right-side mgmt panels)
//   INPUT_ON_SURFACE  — parent is bg-surface (modals/dialogs)
//
// For <select>, compose with ' appearance-none pr-9' and overlay a chevron.
// ---------------------------------------------------------------------------

export const LABEL_CLASS =
  'block text-sm text-text-secondary dark:text-text-secondary-dark mb-1';

// Tailwind emits `dark:` variants AFTER `focus:` variants in the cascade,
// so `dark:border-border-dark` wins over `focus:border-accent` on focused
// inputs in dark mode unless we stack them as `dark:focus:border-accent`.
const INPUT_BASE =
  'w-full px-3 py-2 rounded-lg border border-border dark:border-border-dark text-text-primary dark:text-text-primary-dark placeholder:text-text-tertiary dark:placeholder:text-text-tertiary-dark focus:outline-none focus:border-accent dark:focus:border-accent disabled:opacity-40';

export const INPUT_ON_PANEL = `${INPUT_BASE} bg-surface dark:bg-surface-dark`;
export const INPUT_ON_SURFACE = `${INPUT_BASE} bg-bg dark:bg-bg-dark`;

// ---------------------------------------------------------------------------
// Buttons
//
// Padding / sizing / radius are intentionally NOT baked in — different
// contexts use different combinations (form-footer rounded-lg px-6 py-2,
// modal-footer rounded-lg px-8 py-2, inline-toolbar rounded-md px-4 py-1.5
// text-xs). Constants only normalize color / hover / disabled boilerplate.
// All disabled states get `cursor-not-allowed` (previously inconsistent).
// ---------------------------------------------------------------------------

export const BUTTON_PRIMARY =
  'bg-accent text-white font-medium hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

export const BUTTON_SECONDARY =
  'border border-border dark:border-border-dark text-text-secondary dark:text-text-secondary-dark font-medium bg-surface dark:bg-surface-dark hover:bg-bg dark:hover:bg-bg-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

export const BUTTON_DANGER =
  'bg-status-error text-white font-medium hover:bg-status-error/80 disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

export const BUTTON_DANGER_OUTLINE =
  'border border-status-error text-status-error font-medium bg-surface dark:bg-surface-dark hover:bg-status-error/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

// Ghost / icon buttons (toolbar icons, pagination arrows, close buttons).
// The hover fill is a translucent ink wash so the same recipe works on any
// parent surface (white panel, bg-chat, sidebar accent) in both modes —
// solid hover fills (bg-surface / bg-bg) go invisible on same-colored parents.
// Padding/size stay caller-owned, same as the buttons above.
export const BUTTON_GHOST_ICON =
  'rounded-md text-text-secondary dark:text-text-secondary-dark hover:text-text-primary dark:hover:text-text-primary-dark hover:bg-text-primary/5 dark:hover:bg-text-primary-dark/10 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent transition-colors';

// ---------------------------------------------------------------------------
// Interactive rows (menu items, nav rows, list rows, disclosure headers)
//
// The single canonical hover wash for any transparent-backed clickable row.
// White-in-light / black-in-dark makes it parent-INDEPENDENT: it lightens on
// any light surface and darkens on any dark one. That's the whole point — a
// fixed token (e.g. hover:bg-chat/60) silently goes invisible whenever it lands
// on a parent of its own color (chat==bg on the user-menu popover, panel-accent-
// dark==panel-accent-dark in dark mode), which is how four hand-rolled wash
// recipes drifted apart and the dark-mode user menu lost its hover entirely.
// Direction is house-standard: light lightens, dark darkens.
//
// Compose at the call site with the row's own text color, rounded-*, padding.
// transition-colors is baked in (every prior recipe carried it).
// Do NOT use for filled cards (they have a base bg — lighten within their own
// color) or accent/selected states (those want the brand hue, not a neutral wash).
// ---------------------------------------------------------------------------

export const MENU_ROW_HOVER =
  'hover:bg-white/50 dark:hover:bg-black/25 transition-colors';

// Danger row (logout, delete). The red tint is already parent-independent, so
// it stays a status-error wash — no white/black. For destructive rows only;
// solid red buttons (bg-status-error base) are a different pattern.
export const MENU_ROW_DANGER_HOVER =
  'hover:bg-status-error/10 transition-colors';

// Compact <select> for dense toolbars (artifact toolbar, pagination,
// observability). Full-size selects compose INPUT_ON_PANEL/_ON_SURFACE with
// ' appearance-none pr-9' + <SelectChevron /> instead.
export const SELECT_COMPACT =
  'appearance-none pl-2 pr-7 py-1 text-xs rounded-md border border-border dark:border-border-dark bg-surface dark:bg-surface-dark text-text-secondary dark:text-text-secondary-dark focus:outline-none focus:border-accent dark:focus:border-accent disabled:opacity-40 disabled:cursor-not-allowed';
