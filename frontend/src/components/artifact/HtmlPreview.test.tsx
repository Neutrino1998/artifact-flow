import { describe, expect, it } from 'vitest';
import { HTML_PREVIEW_SANDBOX, withPreviewCsp } from './HtmlPreview';

describe('withPreviewCsp', () => {
  it('inserts the preview CSP first inside an existing head', () => {
    const html = '<!doctype html><html><head><title>Deck</title></head><body><a href="#slide-4">4</a></body></html>';

    const preview = withPreviewCsp(html);

    expect(preview).toContain('<head>\n<meta http-equiv="Content-Security-Policy"');
    expect(preview).toContain("script-src 'none'");
    expect(preview.indexOf('Content-Security-Policy')).toBeLessThan(preview.indexOf('<title>Deck</title>'));
  });

  it('adds a head for html documents without one', () => {
    const html = '<html><body><a href="#slide-4">4</a></body></html>';

    const preview = withPreviewCsp(html);

    expect(preview).toContain('<html>\n<head><meta http-equiv="Content-Security-Policy"');
    expect(preview).toContain('</head><body><a href="#slide-4">4</a></body></html>');
  });

  it('adds a head for html fragments', () => {
    const html = '<a href="#slide-4">4</a><section id="slide-4">Slide</section>';

    const preview = withPreviewCsp(html);

    expect(preview).toContain('<head><meta http-equiv="Content-Security-Policy"');
    expect(preview).toContain("</head>\n<a href=\"#slide-4\">4</a><section id=\"slide-4\">Slide</section>");
  });

  it('keeps same-origin sandboxing so blob fragment links can scroll natively', () => {
    expect(HTML_PREVIEW_SANDBOX).toBe('allow-same-origin');
  });
});
