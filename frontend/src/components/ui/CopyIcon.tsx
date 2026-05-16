'use client';

/**
 * Stateless SVG that swaps between the "copy" and "copied" (checkmark) glyph.
 * Pair with `useCopyFeedback()` for state. Stroke uses `currentColor` so the
 * parent button controls the color.
 */
export function CopyIcon({
  copied,
  size = 14,
}: {
  copied: boolean;
  size?: number;
}) {
  if (copied) {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}
