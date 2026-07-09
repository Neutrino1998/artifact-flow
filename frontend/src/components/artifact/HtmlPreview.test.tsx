import { describe, expect, it } from 'vitest';
import { HTML_PREVIEW_SANDBOX, withPreviewCsp } from './HtmlPreview';

function parsePreview(content: string): Document {
  return new DOMParser().parseFromString(withPreviewCsp(content), 'text/html');
}

function previewCspMeta(document: Document): HTMLMetaElement | null {
  return document.head.querySelector('meta[http-equiv="Content-Security-Policy"]');
}

describe('withPreviewCsp', () => {
  it('inserts the preview CSP first inside an existing head', () => {
    const html = '<!doctype html><html><head><title>Deck</title></head><body><a href="#slide-4">4</a></body></html>';

    const preview = parsePreview(html);
    const meta = previewCspMeta(preview);

    expect(preview.head.firstElementChild).toBe(meta);
    expect(meta?.content).toContain("script-src 'none'");
    expect(preview.head.querySelector('title')?.textContent).toBe('Deck');
  });

  it('adds a head for html documents without one', () => {
    const html = '<html><body><a href="#slide-4">4</a></body></html>';

    const preview = parsePreview(html);

    expect(preview.compatMode).toBe('BackCompat');
    expect(previewCspMeta(preview)?.content).toContain("default-src 'none'");
    expect(preview.body.querySelector('a')?.getAttribute('href')).toBe('#slide-4');
  });

  it('adds a head for html fragments', () => {
    const html = '<a href="#slide-4">4</a><section id="slide-4">Slide</section>';

    const preview = parsePreview(html);

    expect(previewCspMeta(preview)?.content).toContain("default-src 'none'");
    expect(preview.body.querySelector('a')?.getAttribute('href')).toBe('#slide-4');
    expect(preview.body.querySelector('#slide-4')?.textContent).toBe('Slide');
  });

  it('does not inject the preview CSP into fake head tags inside inert text', () => {
    const html =
      '<!-- <head> --><script>const marker = "<head>";</script><img src="https://attacker.example/pixel">';

    const previewHtml = withPreviewCsp(html);
    const preview = new DOMParser().parseFromString(previewHtml, 'text/html');
    const meta = previewCspMeta(preview);

    expect(preview.head.firstElementChild).toBe(meta);
    expect(meta?.content).toContain("img-src data: blob:");
    expect(previewHtml.indexOf('Content-Security-Policy')).toBeLessThan(
      previewHtml.indexOf('https://attacker.example/pixel')
    );
    expect(preview.head.querySelector('script')?.textContent).toContain('<head>');
    expect(preview.body.querySelector('img')?.getAttribute('src')).toBe('https://attacker.example/pixel');
  });

  it('keeps same-origin sandboxing so blob fragment links can scroll natively', () => {
    expect(HTML_PREVIEW_SANDBOX).toBe('allow-same-origin');
  });
});
