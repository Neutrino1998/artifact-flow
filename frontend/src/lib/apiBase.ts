/**
 * Single source of truth for the backend origin used by REST (api.ts), SSE
 * (sse.ts), AND the CSP connect-src (middleware.ts). These MUST agree — if the
 * CSP omits the origin the client actually calls, the browser blocks every API
 * request. They previously drifted (the client defaulted undefined→localhost
 * while the CSP did not), which is why this lives in one place now.
 *
 * Resolution (note `??`, not `||`, so the prod empty string is preserved):
 *   - undefined (fresh clone, no .env.local) → dev default localhost:8000
 *   - ""        (prod build sets NEXT_PUBLIC_API_URL=) → same-origin, relative /api
 *   - explicit value                                   → that origin
 */
export const DEFAULT_API_URL = 'http://localhost:8000';

export const API_URL: string = process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL;
