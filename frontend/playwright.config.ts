import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.ARTIFACTFLOW_PLAYWRIGHT_PORT);
if (!Number.isInteger(port) || port < 1) {
  throw new Error('ARTIFACTFLOW_PLAYWRIGHT_PORT must be set by e2e/run-playwright.mjs');
}

const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // A single Next dev server compiles routes on demand. Keeping two browser
  // workers avoids four cold route compilations contending on the same server.
  workers: 2,
  reporter: process.env.CI ? 'github' : 'list',
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npm run dev -- --hostname 127.0.0.1 --port ${port}`,
    url: `${baseURL}/login`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_URL: '',
    },
  },
});
