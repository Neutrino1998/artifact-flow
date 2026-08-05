import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  MaintenanceNavigation,
  fetchWithMaintenanceRedirect,
  isMaintenanceResponse,
  redirectIfMaintenance,
} from './maintenance';

function response(status: number, headers?: Record<string, string>): Response {
  return new Response(status === 503 ? '<html>maintenance</html>' : '{}', {
    status,
    headers,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('maintenance response handling', () => {
  it('recognizes only the Caddy maintenance response shape', () => {
    expect(isMaintenanceResponse(response(503, {
      'Content-Type': 'text/html; charset=utf-8',
      'Retry-After': '60',
    }))).toBe(true);
    expect(isMaintenanceResponse(response(503, {
      'Content-Type': 'application/json',
      'Retry-After': '60',
    }))).toBe(false);
    expect(isMaintenanceResponse(response(503, {
      'Content-Type': 'text/html',
    }))).toBe(false);
  });

  it('reloads the top-level document for maintenance', () => {
    const reload = vi.fn();
    expect(redirectIfMaintenance(response(503, {
      'Content-Type': 'text/html',
      'Retry-After': '60',
    }), reload)).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
  });

  it('stops the caller after starting maintenance navigation', async () => {
    const fetchMock = vi.fn(async () => response(503, {
      'Content-Type': 'text/html',
      'Retry-After': '60',
    }));
    vi.stubGlobal('fetch', fetchMock);
    const reload = vi.fn();

    await expect(fetchWithMaintenanceRedirect('/api/v1/meta', undefined, reload))
      .rejects.toBeInstanceOf(MaintenanceNavigation);
    expect(reload).toHaveBeenCalledOnce();
  });
});
