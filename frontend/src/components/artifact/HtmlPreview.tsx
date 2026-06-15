'use client';

/**
 * Static preview for `text/html` artifacts.
 *
 * Security model — two orthogonal gates, both shut to the minimum:
 *  1. `sandbox=""` (empty = ALL restrictions on): NO script execution, opaque
 *     origin (can't reach parent token/localStorage/DOM), no form submit, no
 *     top-navigation, no popups. The whole token-theft / same-origin-XSS class
 *     simply has no execution body here.
 *  2. CSP inheritance: a `srcdoc` document inherits the embedding page's CSP, so
 *     the app's strict policy (img-src no `https:`, connect-src 'self', strict
 *     script-src) applies for free — external beacon exfil is already closed.
 *     For static HTML this inheritance is a bonus, not an obstacle.
 *
 * Net capability: render HTML + inline CSS + `data:` images. No JS, no external
 * resources. Verified `frame-src 'self'` permits `srcdoc` in Chrome + Safari;
 * the parent CSP carries the matching `frame-src 'self'` (see lib/csp.ts).
 *
 * Interactive (JS-running) HTML is deliberately NOT this component — that needs
 * a harder boundary (separate origin / allow-scripts sandbox) and is deferred.
 */
export default function HtmlPreview({ content }: { content: string }) {
  return (
    <iframe
      title="HTML preview"
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={content}
      className="block w-full h-full border-0 bg-white"
    />
  );
}
