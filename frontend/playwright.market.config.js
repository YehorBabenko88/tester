import { defineConfig } from "@playwright/test";
const target = process.env.TESTER_MARKET_BASE_URL;
if (
  process.env.TESTER_MARKET_OPT_IN !== "fresh-owned-market" ||
  !target ||
  !/^[a-f0-9]{40}$/.test(process.env.TESTER_MARKET_SOURCE_SHA || "")
)
  throw Error(
    "Explicit fresh-owned-market opt-in, loopback base URL and exact source SHA required",
  );
const url = new URL(target);
if (
  url.protocol !== "http:" ||
  url.hostname !== "127.0.0.1" ||
  url.port !== "18767" ||
  url.pathname !== "/" ||
  url.search ||
  url.hash ||
  url.username ||
  url.password
)
  throw Error("Only the owned market lab at http://127.0.0.1:18767 is allowed");
export default defineConfig({
  testDir: "./tests",
  testMatch: "market-backend.e2e.js",
  workers: 1,
  retries: 0,
  timeout: 240000,
  use: {
    baseURL: target,
    headless: true,
    deviceScaleFactor: 1,
    viewport: { width: 1600, height: 1000 },
  },
  reporter: "line",
});
