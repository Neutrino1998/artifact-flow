const MAINTENANCE_RETRY_AFTER = '60';

export class MaintenanceNavigation extends Error {
  constructor() {
    super('Navigating to maintenance page');
    this.name = 'MaintenanceNavigation';
  }
}

/**
 * Caddy's maintenance gate returns a 503 HTML document with Retry-After: 60.
 * Status alone is insufficient: an application/backend 503 must remain a normal
 * API error instead of replacing the whole page.
 */
export function isMaintenanceResponse(response: Response): boolean {
  const contentType = response.headers.get('Content-Type')?.toLowerCase() ?? '';
  return response.status === 503
    && response.headers.get('Retry-After') === MAINTENANCE_RETRY_AFTER
    && contentType.includes('text/html');
}

export function redirectIfMaintenance(
  response: Response,
  reload: () => void = () => window.location.reload(),
): boolean {
  if (!isMaintenanceResponse(response)) return false;
  reload();
  return true;
}

export async function fetchWithMaintenanceRedirect(
  input: RequestInfo | URL,
  init?: RequestInit,
  reload?: () => void,
): Promise<Response> {
  const response = await fetch(input, init);
  if (redirectIfMaintenance(response, reload)) {
    throw new MaintenanceNavigation();
  }
  return response;
}
