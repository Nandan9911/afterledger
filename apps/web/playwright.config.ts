/**
 * Browser tests (Playwright, with axe for accessibility) against the API and the web app.
 *
 * `python tools/dev.py e2e` runs them. If the API (8080) and the web app (3000) are already running (python
 * tools/dev.py dev), they are used; otherwise both are started here, offline, on a scratch state directory, with
 * the demo bundles from `python tools/dev.py demos` (AL_DEMO_BUNDLES, default ../../.data/demo_bundles).
 *
 * PLAYWRIGHT_CHROMIUM_PATH points at a Chromium to use instead of Playwright's own download.
 */
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, devices } from '@playwright/test';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..', '..');
const api = path.join(root, 'services', 'api');
const windows = process.platform === 'win32';
const python = path.join(api, '.venv', windows ? 'Scripts/python.exe' : 'bin/python');
const state = path.join(os.tmpdir(), `afterledger-e2e-${process.pid}`);
const bundles = process.env.AL_DEMO_BUNDLES ?? path.join(root, '.data', 'demo_bundles');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined;

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1360, height: 900 },
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1360, height: 900 },
        launchOptions: {
          executablePath,
          // a microphone that plays a test tone, granted without asking (for the voice test)
          args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
        },
        permissions: ['microphone'],
      },
    },
  ],
  webServer: [
    {
      command: `"${python}" -m uvicorn afterledger.app.main:app --port 8080`,
      cwd: api,
      url: 'http://localhost:8080/v1/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        AL_MODE: 'offline',
        AL_LLM: 'scripted',
        AL_STORE: 'local',
        AL_STATE_DIR: state,
        AL_DEMO_BUNDLES: bundles,
        AL_SEED_ON_START: 'true',
        AL_CORS_ORIGINS: 'http://localhost:3000',
        PYTHONUTF8: '1',
      },
    },
    {
      command: 'pnpm exec next dev --port 3000',
      cwd: here,
      url: 'http://localhost:3000/en',
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { AL_API_BASE: 'http://localhost:8080', NEXT_TELEMETRY_DISABLED: '1' },
    },
  ],
});
