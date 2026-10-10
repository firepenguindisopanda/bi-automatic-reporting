import { defineConfig, devices } from '@playwright/test'

const PORT = 4173
const ORIGIN = `http://127.0.0.1:${PORT}`
const CI = Boolean(process.env.CI)

/**
 * End-to-end tests run against the production bundle served by `vite preview`.
 * Every /api call is mocked in the test itself, so no backend is needed.
 */
export default defineConfig({
  testDir: './e2e',
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  reporter: CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: ORIGIN,
    trace: 'on-first-retry',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite build && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `${ORIGIN}/reports/`,
    reuseExistingServer: !CI,
    timeout: 120_000,
  },
})
