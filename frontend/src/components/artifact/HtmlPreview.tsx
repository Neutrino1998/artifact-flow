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
const SRCDOC_BASE_TAG = '<base href="about:srcdoc" />';

export function withSrcdocBase(content: string): string {
  const headOpen = /<head(?:\s[^>]*)?>/i;
  if (headOpen.test(content)) {
    return content.replace(headOpen, (match) => `${match}\n${SRCDOC_BASE_TAG}`);
  }

  const htmlOpen = /<html(?:\s[^>]*)?>/i;
  if (htmlOpen.test(content)) {
    return content.replace(htmlOpen, (match) => `${match}\n<head>${SRCDOC_BASE_TAG}</head>`);
  }

  const doctype = /<!doctype html[^>]*>/i;
  if (doctype.test(content)) {
    return content.replace(doctype, (match) => `${match}\n<head>${SRCDOC_BASE_TAG}</head>`);
  }

  return `<head>${SRCDOC_BASE_TAG}</head>\n${content}`;
}

export default function HtmlPreview({ content }: { content: string }) {
  return (
    <iframe
      title="HTML preview"
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={withSrcdocBase(content)}
      className="block w-full h-full border-0 bg-white"
    />
  );
}
