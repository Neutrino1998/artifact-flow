import { describe, it, expect } from 'vitest';
import { buildContentSecurityPolicy, buildSecurityHeaders } from './csp';

function directives(csp: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const part of csp.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf(' ');
    if (idx === -1) {
      map.set(trimmed, '');
    } else {
      map.set(trimmed.slice(0, idx), trimmed.slice(idx + 1));
    }
  }
  return map;
}

describe('buildContentSecurityPolicy', () => {
  it('nonces script-src and keeps strict-dynamic', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'abc123', isDev: false });
    const d = directives(csp);
    expect(d.get('script-src')).toContain("'nonce-abc123'");
    expect(d.get('script-src')).toContain("'strict-dynamic'");
    expect(d.get('script-src')).toContain("'self'");
  });

  it('prod (no apiUrl) keeps connect-src same-origin only', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: false });
    expect(directives(csp).get('connect-src')).toBe("'self'");
  });

  it('prod script-src has no unsafe-eval', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: false });
    expect(directives(csp).get('script-src')).not.toContain("'unsafe-eval'");
  });

  it('whitelists the cross-origin API backend in connect-src', () => {
    const csp = buildContentSecurityPolicy({
      nonce: 'n',
      isDev: false,
      apiUrl: 'http://localhost:8000',
    });
    expect(directives(csp).get('connect-src')).toContain('http://localhost:8000');
  });

  it('uses only the origin of apiUrl, dropping path/query', () => {
    const csp = buildContentSecurityPolicy({
      nonce: 'n',
      isDev: false,
      apiUrl: 'https://api.example.com/v1/?x=1',
    });
    const connect = directives(csp).get('connect-src')!;
    expect(connect).toContain('https://api.example.com');
    expect(connect).not.toContain('/v1');
  });

  it('dev adds unsafe-eval and websocket transport', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: true });
    const d = directives(csp);
    expect(d.get('script-src')).toContain("'unsafe-eval'");
    expect(d.get('connect-src')).toContain('ws:');
    expect(d.get('connect-src')).toContain('wss:');
  });

  it('keeps img-src closed to remote https (exfil channel shut)', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: false });
    const img = directives(csp).get('img-src')!;
    expect(img).toBe("'self' data: blob:");
    expect(img).not.toContain('https:');
  });

  it('locks framing, objects, and base-uri', () => {
    const d = directives(buildContentSecurityPolicy({ nonce: 'n', isDev: false }));
    expect(d.get('frame-ancestors')).toBe("'none'");
    expect(d.get('object-src')).toBe("'none'");
    expect(d.get('base-uri')).toBe("'none'");
  });

  it('treats empty-string apiUrl as same-origin (prod build sets "")', () => {
    // prod sets NEXT_PUBLIC_API_URL="" → apiBase preserves it via ?? → same-origin.
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: false, apiUrl: '' });
    expect(directives(csp).get('connect-src')).toBe("'self'");
  });

  it('tolerates an unparseable apiUrl (falls back to self)', () => {
    const csp = buildContentSecurityPolicy({ nonce: 'n', isDev: false, apiUrl: 'not a url' });
    expect(directives(csp).get('connect-src')).toBe("'self'");
  });
});

describe('buildSecurityHeaders', () => {
  it('denies framing and sniffing', () => {
    const h = buildSecurityHeaders();
    expect(h['X-Frame-Options']).toBe('DENY');
    expect(h['X-Content-Type-Options']).toBe('nosniff');
    expect(h['Referrer-Policy']).toBe('strict-origin-when-cross-origin');
  });
});
