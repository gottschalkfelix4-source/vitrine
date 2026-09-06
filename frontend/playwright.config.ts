import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 6000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4173", viewport: { width: 390, height: 844 },
    isMobile: true, hasTouch: true, serviceWorkers: "block", trace: "retain-on-failure", screenshot: "only-on-failure",
  },
  projects: [
    { name: "Chromium Touch", use: { browserName: "chromium", launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } } },
    { name: "WebKit Touch", use: { browserName: "webkit", launchOptions: { executablePath: process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH } } },
  ],
  webServer: { command: "npm run build && npm run preview -- --host 127.0.0.1 --port 4173", url: "http://127.0.0.1:4173", reuseExistingServer: !process.env.CI },
});
