'use client';

import { useEffect, useState } from 'react';

/**
 * Static preview for `text/html` artifacts.
 *
 * Security model — two orthogonal gates, both shut to the minimum:
 *  1. `sandbox="allow-same-origin"`: NO script execution, no form submit, no
 *     top-navigation, no popups. Keeping the blob document's origin is needed
 *     for native `href="#section"` scrolling; script execution remains the
 *     active boundary for parent token/localStorage/DOM access.
 *  2. Preview CSP: the blob document starts with a restrictive meta CSP that
 *     keeps scripts, connections, forms, frames, and external resources closed.
 *     Inline CSS and data/blob images are allowed for static authored previews.
 *
 * Net capability: render HTML + inline CSS + `data:` images. No JS, no external
 * resources. The parent CSP permits `frame-src blob:` (see lib/csp.ts). Using a
 * blob URL gives the document its own URL, so `href="#section"` stays inside the
 * preview frame instead of resolving to the host app URL as `srcdoc` does.
 *
 * Interactive (JS-running) HTML is deliberately NOT this component — that needs
 * a harder boundary (separate origin / allow-scripts sandbox) and is deferred.
 */
const PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'none'",
  "connect-src 'none'",
  "img-src data: blob:",
  "style-src 'unsafe-inline'",
  "font-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

export const HTML_PREVIEW_SANDBOX = 'allow-same-origin';

function makePreviewCspMeta(document: Document): HTMLMetaElement {
  const meta = document.createElement('meta');
  meta.httpEquiv = 'Content-Security-Policy';
  meta.content = PREVIEW_CSP;
  return meta;
}

function serializeDoctype(doctype: DocumentType | null): string | null {
  if (!doctype) return null;

  if (doctype.publicId) {
    const systemId = doctype.systemId ? ` "${doctype.systemId}"` : '';
    return `<!doctype ${doctype.name} PUBLIC "${doctype.publicId}"${systemId}>`;
  }

  if (doctype.systemId) {
    return `<!doctype ${doctype.name} SYSTEM "${doctype.systemId}">`;
  }

  return `<!doctype ${doctype.name}>`;
}

export function withPreviewCsp(content: string): string {
  const document = new DOMParser().parseFromString(content, 'text/html');
  document.head.prepend(makePreviewCspMeta(document));
  const doctype = serializeDoctype(document.doctype);
  return doctype ? `${doctype}\n${document.documentElement.outerHTML}` : document.documentElement.outerHTML;
}

export default function HtmlPreview({ content }: { content: string }) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    const blob = new Blob([withPreviewCsp(content)], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    setPreviewUrl(url);

    return () => URL.revokeObjectURL(url);
  }, [content]);

  if (!previewUrl) {
    return <div className="block w-full h-full border-0 bg-white" />;
  }

  return (
    <iframe
      title="HTML preview"
      sandbox={HTML_PREVIEW_SANDBOX}
      referrerPolicy="no-referrer"
      src={previewUrl}
      className="block w-full h-full border-0 bg-white"
    />
  );
}
