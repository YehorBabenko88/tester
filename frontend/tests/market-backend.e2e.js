import { test, expect } from "@playwright/test";
import { compareDecimal } from "../model.js";
const LEGACY_TEN = new Set([
  "BTCUSDT",
  "ETHUSDT",
  "SOLUSDT",
  "XRPUSDT",
  "DOGEUSDT",
  "ADAUSDT",
  "AVAXUSDT",
  "LINKUSDT",
  "DOTUSDT",
  "LTCUSDT",
]);
const unwrap = async (response) => {
  expect(response.ok()).toBeTruthy();
  const p = await response.json();
  expect(p.schema_version).toBe("1.0");
  return p.data;
};

test("real cached catalog global sorting and persistent queue download outside legacy ten", async ({
  page,
  request,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let catalog;
  await expect
    .poll(
      async () => {
        catalog = await unwrap(
          await request.get(
            "/api/market-catalog/linear?limit=50&status=Trading&contract_kind=perpetual",
          ),
        );
        return catalog.catalog.completeness;
      },
      { timeout: 180000, intervals: [1000, 2000, 5000] },
    )
    .toBe("EXHAUSTED");
  expect(catalog.total).toBeGreaterThan(10);
  expect(catalog.rows.length).toBeGreaterThan(10);
  const chosen = catalog.rows.find(
    (r) => !LEGACY_TEN.has(r.symbol) && r.category === "linear",
  );
  expect(
    chosen,
    "Real source must expose an instrument outside the old ten",
  ).toBeTruthy();
  await page.goto("/");
  await expect(page.locator("[data-row]")).toHaveCount(
    Math.min(50, catalog.total),
  );
  const header = page.getByRole("button", { name: "Sort Price", exact: true });
  for (const direction of ["asc", "desc", "none"]) {
    const response = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/market-catalog/linear" &&
        new URL(r.url()).searchParams.get("sort") === "price" &&
        new URL(r.url()).searchParams.get("direction") === direction,
    );
    await header.click();
    const data = await unwrap(await response);
    if (direction !== "none") {
      const values = data.rows.map((r) => r.metrics.price.value);
      let absent = false;
      for (let i = 0; i < values.length; i++) {
        if (values[i] == null) {
          absent = true;
          continue;
        }
        expect(absent, "Unavailable metrics must sort last").toBe(false);
        if (i && values[i - 1] != null)
          expect(
            compareDecimal(
              values[i - 1],
              values[i],
              direction === "asc" ? 1 : -1,
            ),
          ).toBeLessThanOrEqual(0);
      }
    }
  }
  await page.getByLabel("Search symbols").fill(chosen.symbol);
  const row = page.locator(`[data-row="linear:${chosen.symbol}"]`);
  await expect(row).toBeVisible();
  let submits = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/downloads")
      submits++;
  });
  await page.getByLabel(`Select ${chosen.symbol}`, { exact: true }).check();
  await page
    .getByRole("button", { name: `Chart ${chosen.symbol}`, exact: true })
    .click();
  expect(submits).toBe(0);
  await page.reload();
  await expect(page.getByLabel("Search symbols")).toHaveValue(chosen.symbol);
  await expect(
    page.getByLabel(`Select ${chosen.symbol}`, { exact: true }),
  ).toBeChecked();
  await page.locator("#download-options").evaluate((e) => (e.open = true));
  await expect(page.locator("#interval option")).toHaveCount(13);
  await page.locator("#source").selectOption("bybit");
  await page.locator("#interval").selectOption("1");
  const end = Math.floor((Date.now() - 5 * 60000) / 60000) * 60000,
    start = end - 10 * 60000;
  await page
    .getByLabel("Range start UTC")
    .fill(new Date(start).toISOString().slice(0, 16));
  await page
    .getByLabel("Range end UTC")
    .fill(new Date(end).toISOString().slice(0, 16));
  const accepted = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      new URL(r.url()).pathname === "/api/downloads",
  );
  await page
    .getByRole("button", { name: `Download ${chosen.symbol}`, exact: true })
    .click();
  const job = await unwrap(await accepted);
  expect(job.id).toBeTruthy();
  expect(submits).toBe(1);
  const inline = row.locator(`[data-job-id="${job.id}"]`);
  await expect(inline).toContainText("Download COMPLETE", { timeout: 180000 });
  await expect(inline).toContainText("Coverage UNKNOWN");
  const persisted = await unwrap(await request.get(`/api/downloads/${job.id}`));
  expect(persisted.state).toBe("completed");
  expect(persisted.request.symbol).toBe(chosen.symbol);
  const telemetry = persisted.checkpoint.telemetry;
  expect(telemetry.records_ingested).toBeGreaterThan(0);
  expect(telemetry.bytes_received).toBeGreaterThan(0);
  expect(telemetry.coverage).toBe("UNKNOWN");
  expect(errors).toEqual([]);
  await page.locator("#download-options").evaluate((e) => (e.open = false));
  if (process.env.TESTER_MARKET_SCREENSHOT) {
    await page
      .locator(".eyebrow")
      .evaluate(
        (e, sha) => (e.textContent = `ACTUAL OWNED MARKET BACKEND - ${sha}`),
        process.env.TESTER_MARKET_SOURCE_SHA,
      );
    await page
      .locator("#screener")
      .screenshot({ path: process.env.TESTER_MARKET_SCREENSHOT });
  }
  console.log(
    JSON.stringify({
      application_source_sha: process.env.TESTER_MARKET_SOURCE_SHA,
      catalog_revision: catalog.revision,
      catalog_loaded: catalog.catalog.loaded,
      symbol: chosen.symbol,
      job_id: job.id,
      download: persisted.state,
      coverage: telemetry.coverage,
      records_ingested: telemetry.records_ingested,
      bytes_received: telemetry.bytes_received,
    }),
  );
});
