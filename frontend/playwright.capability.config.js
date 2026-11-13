import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "capability-backend.e2e.js",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:18874",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
