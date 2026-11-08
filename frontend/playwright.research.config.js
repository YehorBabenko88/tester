import { defineConfig } from "@playwright/test";
const target = process.env.TESTER_RESEARCH_BASE_URL;
if (!target || process.env.TESTER_RESEARCH_OPT_IN !== "synthetic-disposable")
  throw Error(
    "Explicit disposable synthetic backend required: TESTER_RESEARCH_BASE_URL and TESTER_RESEARCH_OPT_IN=synthetic-disposable",
  );
const url = new URL(target);
if (
  url.protocol !== "http:" ||
  !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
)
  throw Error("Use the root-provided disposable loopback research backend");
export default defineConfig({
  testDir: "./tests",
  testMatch: "research-backend.e2e.js",
  workers: 1,
  use: { baseURL: target, headless: true, deviceScaleFactor: 2 },
  timeout: 60000,
});
