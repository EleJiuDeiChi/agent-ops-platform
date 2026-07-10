import { defineConfig, devices } from "@playwright/test";

const localBrowserChannel = process.env.PLAYWRIGHT_BROWSER_CHANNEL?.trim();
const e2ePort = process.env.PLAYWRIGHT_E2E_PORT?.trim() || "55173";
const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL?.trim();
const e2eBaseUrl = externalBaseUrl || `http://127.0.0.1:${e2ePort}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "list",
  use: {
    baseURL: e2eBaseUrl,
    ignoreHTTPSErrors: process.env.PLAYWRIGHT_IGNORE_HTTPS_ERRORS === "1",
    trace: "on-first-retry"
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: `npm run dev -- --port ${e2ePort} --strictPort`,
        url: e2eBaseUrl,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000
      },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        channel: localBrowserChannel || undefined
      }
    }
  ]
});
