import { defineConfig } from '@playwright/test';

const useExternalServer = Boolean(process.env.E2E_BASE_URL);

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.spec.ts',
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5173',
    headless: true,
    acceptDownloads: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: useExternalServer
    ? undefined
    : [
        {
          command:
            "cd ../backend && DATABASE_URL=sqlite+aiosqlite:////tmp/qualitext_e2e.db LOCAL_STORAGE_PATH=/tmp/qualitext_e2e_storage CELERY_TASK_ALWAYS_EAGER=true CORS_ORIGINS='[\"http://127.0.0.1:5173\",\"http://localhost:5173\"]' .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001",
          url: 'http://127.0.0.1:8001/healthz',
          reuseExistingServer: false,
          timeout: 30_000,
        },
        {
          command:
            'VITE_API_BASE_URL=http://127.0.0.1:8001/api/v1 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
          url: 'http://127.0.0.1:5173',
          reuseExistingServer: false,
          timeout: 30_000,
        },
      ],
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
});
