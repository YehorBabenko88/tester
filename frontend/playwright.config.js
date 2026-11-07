import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "*.spec.js",
  use: { baseURL: "http://terminal.test", headless: true },
  workers: 1,
});
