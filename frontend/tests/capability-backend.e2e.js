import { test, expect } from "@playwright/test";

const intervals = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];

test("real FastAPI capabilities and price-only routes across 13 UTC timeframes", async ({
  page,
}, testInfo) => {
  // Only the independent catalog is mocked; capabilities and price records
  // always come from the actual FastAPI application running on loopback.
  await page.route("**/api/market-catalog/**", async route => {
    await route.fulfill({ json: {
      schema_version: "1.0", next_cursor: null,
      data: {
        rows: [{ category: "linear", symbol: "BTCUSDT",
                 status: "Trading", metrics: {} }],
        total: 1, revision: "ci-fixture-rev",
        catalog: { loaded: 1, completeness: "UNKNOWN", state: "FIXTURE" },
      },
    } });
  });

  const pageErrors = [], consoleErrors = [], serverErrors = [], forbiddenRequests = [];
  const unsupported = new Set([
    "/api/analytics", "/api/footprint", "/api/open-interest",
    "/api/funding", "/api/candles", "/api/coverage",
  ]);
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error" && /Uncaught|TypeError|ReferenceError|SyntaxError/.test(message.text()))
      consoleErrors.push(message.text());
  });
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/") &&
        [404, 422, 503].includes(response.status()))
      serverErrors.push({ path: url.pathname, status: response.status() });
  });
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (unsupported.has(path)) forbiddenRequests.push(path);
  });

  await page.goto("/");
  await page.locator("#download-options").evaluate(node => { node.open = true; });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-04-01T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();

  const mark = page.locator('[data-panel="mark"]');
  const index = page.locator('[data-panel="index"]');
  const verified = [];
  for (const interval of intervals) {
    // The 60-minute interval is already selected by default.
    const pending = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === "/api/capabilities" &&
        url.searchParams.get("interval") === interval &&
        response.status() === 200;
    });
    await page.locator("#interval").selectOption(interval);
    if (interval === "60") {
      // An unchanged selection may not emit 'change'; explicit retry must.
      await page.getByRole("button", { name: "Apply range / Retry charts" }).click();
    }
    await pending;
    await expect(mark.locator("canvas")).toBeVisible();
    await expect(index.locator("canvas")).toBeVisible();
    await expect(mark.locator(".panel-meta")).toContainText("PARTIAL");
    await expect(index.locator(".panel-meta")).toContainText("PARTIAL");
    await expect(mark.locator(".panel-evidence")).toContainText("mark-price-kline");
    await expect(index.locator(".panel-evidence")).toContainText("index-price-kline");
    verified.push(interval);
  }
  expect(verified).toEqual(intervals);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(serverErrors).toEqual([]);
  expect(forbiddenRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("capability-13-tf.png"), fullPage: true });
});
