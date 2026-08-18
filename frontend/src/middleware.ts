import { NextRequest, NextResponse } from 'next/server';
import { buildContentSecurityPolicy, buildSecurityHeaders } from '@/lib/csp';
import { API_URL } from '@/lib/apiBase';

/**
 * Per-request CSP + hardening headers. The nonce is generated here and pushed
 * back onto the *request* headers so Next stamps it onto its own inline scripts;
 * `x-nonce` carries it to the root layout for our own inline theme script.
 * See src/lib/csp.ts for why CSP lives here rather than in nginx.
 */
export function middleware(request: NextRequest): NextResponse {
  const isPublicSsoCallback = request.nextUrl.pathname === '/auth/sso/callback';
  // Rewrites re-enter middleware with the internal pathname. Both passes must
  // retain the sensitive-document referrer policy.
  const isSsoCallbackDocument = isPublicSsoCallback
    || request.nextUrl.pathname === '/auth/sso/callback-shell';
  const nonce = btoa(crypto.randomUUID());
  const csp = buildContentSecurityPolicy({
    nonce,
    isDev: process.env.NODE_ENV !== 'production',
    // Same resolved origin the REST/SSE client uses (api.ts/sse.ts) — see
    // apiBase.ts. '' stays same-origin (→ connect-src 'self'); undefined falls
    // back to the localhost default so a fresh clone isn't CSP-blocked.
    apiUrl: API_URL,
  });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('content-security-policy', csp);

  // App Router serializes the requested URL (including search params) into its
  // inline RSC payload before client effects can clear location.search. Serve
  // the callback through a queryless internal route so the browser keeps the
  // provider parameters in window.location for the first JS call stack, while
  // Next never receives values it could copy into HTML/DOM.
  const response = isPublicSsoCallback
    ? (() => {
        const callbackShell = request.nextUrl.clone();
        callbackShell.pathname = '/auth/sso/callback-shell';
        callbackShell.search = '';
        return NextResponse.rewrite(callbackShell, { request: { headers: requestHeaders } });
      })()
    : NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('content-security-policy', csp);
  const securityHeaders = buildSecurityHeaders({
    sensitiveCallback: isSsoCallbackDocument,
  });
  for (const [key, value] of Object.entries(securityHeaders)) {
    response.headers.set(key, value);
  }
  return response;
}

export const config = {
  matcher: [
    // Run on document requests only — skip Next's own static output, self-hosted
    // fonts, and favicon (no CSP needed), and skip prefetches so prefetched HTML
    // isn't cached with a stale nonce.
    {
      source: '/((?!api|_next/static|_next/image|favicon.ico|fonts/).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
};
