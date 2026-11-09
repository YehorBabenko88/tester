import { defineConfig } from "@playwright/test";
if (process.env.GITHUB_ACTIONS !== "true" || process.env.TESTER_COMBINED_CI !== "synthetic-disposable")
  throw Error("Hosted disposable combined CI only");
export default defineConfig({
  testDir: "./tests", testMatch: "combined-backend.e2e.js", workers: 1,
  use: { baseURL: "http://127.0.0.1:18768", headless: true, deviceScaleFactor: 2 },
  timeout: 60000,
});
