import { defineConfig } from "@playwright/test";
if (!process.env.TESTER_INSTALLED_URL || !process.env.TESTER_INSTALLED_INSTANCE ||
    !process.env.TESTER_INSTALLED_SOURCE_SHA || !process.env.TESTER_INSTALLED_APP_SHA256)
  throw new Error("Installed SCM browser gate requires independently supplied live identity");
const url = new URL(process.env.TESTER_INSTALLED_URL);
if (url.hostname !== "127.0.0.1" || url.protocol !== "http:")
  throw new Error("Only the isolated loopback installation may be exercised");
export default defineConfig({
  testDir: "./tests", testMatch: "release-installed.spec.js", workers: 1,
  retries: 0, timeout: 60000,
  use: { baseURL: url.origin, headless: true },
  reporter: "line",
});
