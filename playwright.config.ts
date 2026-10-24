import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3101";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { outputFolder: "test-results/e2e-report", open: "never" }]],
  use: {
    baseURL,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command:
      "GMS_DATABASE_PATH=/private/tmp/gmsv5-playwright.sqlite SESSION_SECRET=local-playwright-session-secret-0123456789 GMS_CREDENTIAL_ENCRYPTION_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= NODE_ENV=production npm start -- --hostname 127.0.0.1 --port 3101",
    url: `${baseURL}/`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
