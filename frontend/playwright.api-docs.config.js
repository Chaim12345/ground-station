import process from 'node:process';
import { defineConfig, devices } from '@playwright/test';

// Without BASE_URL, the docs smoke test serves local ASGI responses through
// Playwright routes and needs neither app server running.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'api-docs.spec.js',
  timeout: 60 * 1000,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.BASE_URL || 'http://testserver',
    colorScheme: 'light',
  },
});
