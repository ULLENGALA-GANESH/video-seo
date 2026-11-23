import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const backend = fileURLToPath(new URL('../backend/', import.meta.url));
const localPython = join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = existsSync(localPython) ? localPython : 'python';
const apiPort = Number(process.env.E2E_API_PORT ?? 8765);
if (!Number.isInteger(apiPort) || apiPort < 1 || apiPort > 65535) {
  throw new Error('E2E_API_PORT must be a valid local TCP port.');
}
const baseURL = 'http://127.0.0.1:' + apiPort;
const mongoPort = Number(process.env.E2E_MONGO_PORT ?? 27018);
if (!Number.isInteger(mongoPort) || mongoPort < 1 || mongoPort > 65535) {
  throw new Error('E2E_MONGO_PORT must be a valid local TCP port.');
}

export default defineConfig({
  testDir: './e2e',
  outputDir: '../.local/playwright-results',
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL,
    browserName: 'chromium',
    // The published sample requires H.264/AAC playback.
    channel: 'chrome',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `"${python}" -m uvicorn app:app --host 127.0.0.1 --port ${apiPort}`,
    cwd: backend,
    url: baseURL + '/api/ready',
    reuseExistingServer: false,
    timeout: 40_000,
    env: {
      APP_ORIGIN: baseURL,
      COOKIE_SECURE: 'false',
      INLINE_WORKER: 'true',
      // Exercise setup and empty states without using the developer's Google credentials.
      YOUTUBE_API_KEY: '',
      YOUTUBE_CLIENT_ID: '',
      YOUTUBE_CLIENT_SECRET: '',
      YOUTUBE_TOKEN_KEY: '',
      // Always use the dedicated local test service, regardless of backend/.env.
      MONGO_URI: `mongodb://127.0.0.1:${mongoPort}`,
      DB_NAME: `framewise_e2e_${process.pid}`,
      MEDIA_ROOT: join(tmpdir(), `framewise-e2e-media-${process.pid}`),
      FRONTEND_DIST: fileURLToPath(new URL('./dist/', import.meta.url)),
    },
  },
});
