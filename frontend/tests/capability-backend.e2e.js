import { test, expect } from "@playwright/test";

const intervals = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];

test("real FastAPI capabilities and price-only routes across 13 UTC timeframes", async ({
  page,
}, testInfo) => {
  // No catalog interception: validate real API feature negotiation so an
  // optional, absent /api/market-catalog route cannot produce 404.

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
  await expect(page.locator("#screener-status")).toContainText(
    "market_catalog_not_configured",
  );
  await page.locator("#download-options").evaluate(node => { node.open = true; });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-04-01T00:00");
  await page.getByLabel("Direct source symbol").fill("BTCUSDT");
  await page.getByRole("button", { name: "Open source symbol" }).click();

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
    for (const panel of ["footprint", "delta", "cvd", "open_interest", "funding"]) {
      const unavailable = page.locator(`[data-panel="${panel}"]`);
      await expect(unavailable).toContainText("DATA_UNAVAILABLE");
      await expect(unavailable.locator("canvas")).toBeHidden();
    }
    await expect(page.locator('[data-panel="delta"]')).toContainText(
      "endpoint_contract_not_supported",
    );
    await expect(page.locator('[data-panel="open_interest"]')).toContainText(
      "source_not_connected",
    );
    verified.push(interval);
  }
  expect(verified).toEqual(intervals);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(serverErrors).toEqual([]);
  expect(forbiddenRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("capability-13-tf.png"), fullPage: true });
});
