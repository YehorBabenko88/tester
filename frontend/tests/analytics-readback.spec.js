import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

const html = `<!doctype html><html><body>
<form id="analytics-readback-form">
  <input id="analytics-readback-tick" value="0.5" />
  <input id="analytics-readback-as-of" value="2026-11-26T00:00" />
  <button id="analytics-readback-submit" type="submit">Read sample</button>
</form>
<p id="analytics-readback-status"></p>
<pre id="analytics-readback-data"></pre>
<script type="module">
import { mountObservedReadback } from "/analytics-readback.js";
import { request } from "/api.js";
window.viewer = mountObservedReadback(document, {
  getScope: () => ({
    category: "inverse", symbol: "BTCUSD", interval: "60",
    start: "2026-11-25T00:00", end: "2026-11-25T01:00",
  }),
  fetchCapabilities: (params, signal) =>
    request("/api/capabilities", params, { signal }),
  fetchReadback: (params, signal) =>
    request("/api/analytics/readback", params, { signal }),
});
</script>
</body></html>`;
const enabled = () => ({
  schema_version: "1.0",
  data: { features: { analytics_readback: {
    enabled: true, resource: "/api/analytics/readback",
    status: "OBSERVED_ONLY_NO_F_ADMISSION",
  }}},
});
function response(verified = false) {
  const start = Date.parse("2026-11-25T00:00Z");
  const end = Date.parse("2026-11-25T01:00Z");
  return {
    schema_version: "1.0", data: {
      schema_version: "tester.analytics-readback/1",
      status: "OBSERVED_UNVERIFIED", method: "tester.execution-metrics/2",
      metric_kind: "execution_metrics", metric_method: "tester.execution-metrics/2",
      metric_method_version: "2", historical_data_complete: false,
      strategy_admission: "DENIED", http_route_registered: false,
      admitted_to_f: false,
      quality: { status: verified ? "VERIFIED" : "UNVERIFIED",
        coverage: "UNKNOWN", admitted_to_f: false, historical_data_complete: false,
        reasons: ["historical_coverage_unknown", "publication_time_unknown"] },
      market: { venue: "bybit", category: "inverse", symbol: "BTCUSD" },
      window: { start_ms: start, end_ms: end, interval_ms: 3600000,
        as_of_ms: Date.parse("2026-11-26T00:00Z") },
      source: { publication_time: "UNKNOWN", historical_coverage: "UNKNOWN",
        dataset_revision: null, receipts_sha256: "a".repeat(64),
        data_sha256: "b".repeat(64), metrics_sha256: "c".repeat(64),
        sample_sha256: "d".repeat(64) },
      quantity_unit: "quote",
      source_quantity_unit: "source_native_inverse_contract",
      metrics: {
        method: "tester.execution-metrics/2", status: "DERIVED_UNVERIFIED",
        admitted_to_f: false, quantity_unit: "quote",
        bars: [{ start_ms: start, end_ms: end,
          volume: "100", vwap: "100.5" }],
      },
    },
  };
}
async function serve(page, { availability = enabled(), body = response() } = {}) {
  const requests = [];
  await page.route("http://terminal.test/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/capabilities") {
      requests.push("capabilities");
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(availability) });
    }
    if (url.pathname === "/api/analytics/readback") {
      requests.push("readback");
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    }
    if (url.pathname.endsWith(".js")) {
      return route.fulfill({
        contentType: "text/javascript",
        body: await readFile(path.join(process.cwd(), url.pathname.slice(1))),
      });
    }
    return route.fulfill({ contentType: "text/html", body: html });
  });
  return requests;
}
test("Chromium source-backed observation displays exact sample values without F claim", async ({ page }) => {
  const requests = await serve(page);
  await page.goto("/");
  await page.locator("#analytics-readback-submit").click();
  await expect(page.locator("#analytics-readback-status")).toContainText("OBSERVED_UNVERIFIED");
  await expect(page.locator("#analytics-readback-status")).toContainText("historical coverage UNKNOWN");
  await expect(page.locator("#analytics-readback-status")).toContainText("strategy admission DENIED");
  await expect(page.locator("#analytics-readback-data")).toContainText("100.5");
  expect(requests).toEqual(["capabilities", "readback"]);
});
test("Chromium never requests data from a disabled readback capability", async ({ page }) => {
  const requests = await serve(page, { availability: {
    schema_version: "1.0", data: { features: {
      analytics_readback: { enabled: false, resource: null, status: "NOT_REGISTERED" },
    }},
  }});
  await page.goto("/");
  await page.locator("#analytics-readback-submit").click();
  await expect(page.locator("#analytics-readback-status")).toContainText("DATA_UNAVAILABLE");
  expect(requests).toEqual(["capabilities"]);
});
test("Chromium rejects a forged COMPLETE quality response", async ({ page }) => {
  const requests = await serve(page, { body: response(true) });
  await page.goto("/");
  await page.locator("#analytics-readback-submit").click();
  await expect(page.locator("#analytics-readback-status")).toContainText("DATA_UNAVAILABLE");
  await expect(page.locator("#analytics-readback-data")).toBeEmpty();
  expect(requests).toEqual(["capabilities", "readback"]);
});
