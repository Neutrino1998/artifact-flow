import { describe, expect, it } from 'vitest';
import { withSrcdocBase } from './HtmlPreview';

describe('withSrcdocBase', () => {
  it('inserts the srcdoc base first inside an existing head', () => {
    const html = '<!doctype html><html><head><title>Deck</title></head><body><a href="#slide-4">4</a></body></html>';

    expect(withSrcdocBase(html)).toContain(
      '<head>\n<base href="about:srcdoc" /><title>Deck</title>'
    );
  });

  it('adds a head for html documents without one', () => {
    const html = '<html><body><a href="#slide-4">4</a></body></html>';

    expect(withSrcdocBase(html)).toBe(
      '<html>\n<head><base href="about:srcdoc" /></head><body><a href="#slide-4">4</a></body></html>'
    );
  });

  it('adds a head for html fragments', () => {
    const html = '<a href="#slide-4">4</a><section id="slide-4">Slide</section>';

    expect(withSrcdocBase(html)).toBe(
      '<head><base href="about:srcdoc" /></head>\n<a href="#slide-4">4</a><section id="slide-4">Slide</section>'
    );
  });
});
