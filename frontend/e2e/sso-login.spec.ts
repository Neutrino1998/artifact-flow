import { expect, test, type Page, type Route } from '@playwright/test';

const upstreamToken = 'upstream-token-that-must-not-persist';
const internalToken = 'artifactflow-internal-jwt';
const remoteUser = {
  id: 'remote-user-id',
  // Intentionally the same login name as the local admin below. Identity is
  // selected by provider+subject, never by this display/login attribute.
  username: 'admin',
  display_name: '统一认证用户',
  role: 'user',
  auth_provider: 'company-sso',
  can_change_password: false,
  can_edit_profile: false,
  must_change_password: false,
  department_path: ['研发中心', '平台部'],
};
const localUser = {
  ...remoteUser,
  id: 'local-admin-id',
  username: 'admin',
  display_name: '本地管理员',
  role: 'admin',
  auth_provider: 'local_password',
  can_change_password: true,
  can_edit_profile: true,
  department_path: null,
};

function json(route: Route, body: unknown, headers: Record<string, string> = {}) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers,
    body: JSON.stringify(body),
  });
}

async function browserResidue(page: Page) {
  return page.evaluate(() => ({
    performanceNames: performance.getEntries().map((entry) => entry.name),
    sessionStorageValues: Array.from(
      { length: sessionStorage.length },
      (_, index) => sessionStorage.getItem(sessionStorage.key(index) ?? '') ?? '',
    ),
  }));
}

async function installAuthRoutes(page: Page, options: { cancel?: boolean; expired?: boolean } = {}) {
  const observedMeAuthorizations: string[] = [];
  await page.route('**/api/v1/auth/config', (route) => json(route, {
    password_login_enabled: true,
    sso: {
      enabled: true,
      provider_id: 'company-sso',
      display_name: '企业统一认证',
      token_param: 'ticket',
    },
  }));

  await page.route('**/api/v1/auth/sso/start', (route) => {
    const origin = new URL(route.request().url()).origin;
    const callback = new URL('/auth/sso/callback', origin);
    callback.searchParams.set('af_sso_state', 'browser-state');
    const portal = new URL('/__fake_sso/login', origin);
    portal.searchParams.set('entryPath', callback.toString());
    if (options.cancel) portal.searchParams.set('cancel', '1');
    return json(route, {
      authorization_url: portal.toString(),
      expires_in: 300,
    }, {
      'set-cookie': 'af_sso_binding=fake-binding; HttpOnly; SameSite=Lax; Path=/api/v1/auth/sso/exchange',
    });
  });

  await page.route('**/__fake_sso/login**', (route) => {
    const portal = new URL(route.request().url());
    const callback = new URL(portal.searchParams.get('entryPath')!);
    if (!portal.searchParams.has('cancel')) {
      callback.searchParams.set('ticket', upstreamToken);
    }
    return route.fulfill({ status: 302, headers: { location: callback.toString() } });
  });

  await page.route('**/api/v1/auth/sso/exchange', async (route) => {
    expect(new URL(page.url()).search).toBe('');
    expect(route.request().postDataJSON()).toEqual({
      state: 'browser-state',
      upstream_token: upstreamToken,
    });
    expect(route.request().headers().cookie).toContain('af_sso_binding=fake-binding');
    if (options.expired) {
      return route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'expired state with raw provider diagnostics' }),
      });
    }
    return json(route, {
      access_token: internalToken,
      token_type: 'bearer',
      user: remoteUser,
    }, {
      'set-cookie': 'af_sso_binding=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/api/v1/auth/sso/exchange',
    });
  });

  await page.route('**/api/v1/auth/login', async (route) => {
    expect(route.request().postDataJSON()).toEqual({ username: 'admin', password: 'local-password' });
    return json(route, {
      access_token: 'local-internal-jwt',
      token_type: 'bearer',
      user: localUser,
    });
  });

  await page.route('**/api/v1/auth/me', (route) => {
    const authorization = route.request().headers().authorization;
    if (authorization) observedMeAuthorizations.push(authorization);
    return json(route, authorization === 'Bearer local-internal-jwt' ? localUser : remoteUser);
  });

  return { observedMeAuthorizations };
}

test('creates then reuses the same remote identity without persisting or leaking the upstream token', async ({ page, context }) => {
  const observed = await installAuthRoutes(page);
  const consoleMessages: string[] = [];
  const requestReferrers: string[] = [];
  const downloads: string[] = [];
  page.on('console', (message) => consoleMessages.push(message.text()));
  page.on('request', (request) => requestReferrers.push(request.headers().referer ?? ''));
  page.on('download', (download) => downloads.push(download.suggestedFilename()));

  await page.goto('/login');
  await expect(page.getByRole('button', { name: '企业统一认证' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
  await page.getByRole('button', { name: '企业统一认证' }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('af_token'))).toBe(internalToken);
  const storedUser = await page.evaluate(() => localStorage.getItem('af_user'));
  expect(storedUser).toContain('remote-user-id');
  expect(storedUser).toContain('"username":"admin"');
  expect(storedUser).not.toContain(upstreamToken);
  expect(await context.cookies()).not.toEqual(expect.arrayContaining([
    expect.objectContaining({ value: upstreamToken }),
  ]));
  expect((await context.cookies()).map((cookie) => cookie.name)).not.toContain('af_sso_binding');
  expect(await page.locator('body').textContent()).not.toContain(upstreamToken);
  expect(consoleMessages.join('\n')).not.toContain(upstreamToken);
  expect(requestReferrers.join('\n')).not.toContain(upstreamToken);
  expect(downloads).toEqual([]);
  const residue = await browserResidue(page);
  expect(residue.performanceNames.join('\n')).not.toContain(upstreamToken);
  expect(residue.performanceNames.join('\n')).not.toContain('browser-state');
  expect(residue.sessionStorageValues.join('\n')).not.toContain(upstreamToken);
  await expect.poll(() => observed.observedMeAuthorizations).toContain('Bearer artifactflow-internal-jwt');

  // A later login for the same provider subject reuses the same ArtifactFlow
  // identity returned by the fake userinfo/exchange boundary.
  await page.evaluate(() => localStorage.clear());
  await page.goto('/login');
  await page.getByRole('button', { name: '企业统一认证' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('af_user')))
    .toContain('remote-user-id');
});

test('same-name local emergency login enters the distinct local identity', async ({ page }) => {
  const observed = await installAuthRoutes(page);
  await page.goto('/login');
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('local-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('af_token'))).toBe('local-internal-jwt');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('af_user')))
    .toContain('local-admin-id');
  await expect.poll(() => observed.observedMeAuthorizations).toContain('Bearer local-internal-jwt');
});

test('shows a restart action for provider cancellation without exposing callback details', async ({ page }) => {
  await installAuthRoutes(page, { cancel: true });
  await page.goto('/login');
  await page.getByRole('button', { name: '企业统一认证' }).click();
  await expect(page).toHaveURL(/\/auth\/sso\/callback$/);
  await expect(page.getByText('企业登录未完成，可能已取消。请重新发起登录。')).toBeVisible();
  await expect(page.getByRole('button', { name: '重新发起企业统一认证' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText('browser-state');
  const residue = await browserResidue(page);
  expect(residue.performanceNames.join('\n')).not.toContain('browser-state');
});

test('fails a queryless callback refresh safely', async ({ page }) => {
  await installAuthRoutes(page);
  const response = await page.goto('/auth/sso/callback');
  expect(response?.headers()['referrer-policy']).toBe('no-referrer');
  await expect(page.getByText('本次企业登录信息缺失或已失效，请重新发起登录。')).toBeVisible();
  await expect(page.getByRole('button', { name: '重新发起企业统一认证' })).toBeVisible();
});

test('turns an expired exchange into a sanitized restart prompt', async ({ page }) => {
  await installAuthRoutes(page, { expired: true });
  await page.goto('/login');
  await page.getByRole('button', { name: '企业统一认证' }).click();
  await expect(page).toHaveURL(/\/auth\/sso\/callback$/);
  await expect(page.getByText('本次企业登录已失效或未完成，请重新发起登录。')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('raw provider diagnostics');
  await expect(page.getByRole('button', { name: '重新发起企业统一认证' })).toBeVisible();
  const residue = await browserResidue(page);
  expect(residue.performanceNames.join('\n')).not.toContain(upstreamToken);
  expect(residue.performanceNames.join('\n')).not.toContain('browser-state');
  expect(residue.sessionStorageValues.join('\n')).not.toContain(upstreamToken);
  expect(residue.sessionStorageValues.join('\n')).not.toContain('raw provider diagnostics');
});
