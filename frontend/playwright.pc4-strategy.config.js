import { defineConfig } from "@playwright/test";
if (
  process.env.TESTER_PC4_OPT_IN !== "pc4-owned" ||
  !process.env.TESTER_PC4_STRATEGY_CASE_FILE ||
  !/^[a-f0-9]{40}$/.test(process.env.TESTER_PC4_SOURCE_SHA || "") ||
  !/^[a-f0-9]{40}$/.test(process.env.TESTER_PC4_HARNESS_SHA || "")
)
  throw Error(
    "Private strategy case, pc4-owned opt-in and exact application/harness SHAs required",
  );
const rawScale = process.env.TESTER_PC4_DEVICE_SCALE_FACTOR ?? "1";
if (!["1", "2"].includes(rawScale))
  throw Error("TESTER_PC4_DEVICE_SCALE_FACTOR must be 1 or 2");
const deviceScaleFactor = Number(rawScale);
const channel = process.env.TESTER_PC4_BROWSER_CHANNEL;
if (channel && !["chrome", "msedge"].includes(channel))
  throw Error("Existing Chrome or Edge only; no browser installation");
export default defineConfig({
  testDir: "./tests",
  testMatch: "pc4-owned-strategy.e2e.js",
  workers: 1,
  retries: 0,
  timeout: 180000,
  expect: { timeout: 30000 },
  outputDir: "test-results/pc4-owned-strategy",
  reporter: [
    ["line"],
    ["json", { outputFile: "test-results/pc4-owned-strategy-report.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:18767",
    headless: true,
    ...(channel ? { channel } : {}),
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor,
    trace: "off",
    screenshot: "off",
    video: "off",
    serviceWorkers: "block",
  },
});
