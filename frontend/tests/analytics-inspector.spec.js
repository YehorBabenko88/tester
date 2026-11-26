import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";

const html = `<!doctype html>
<html><body>
<form id="analytics-inspector-form">
<select id="analytics-inspector-metric">
<option value="vwap">VWAP</option>
<option value="hourly_range_percent">Hourly</option>
</select>
<button id="analytics-inspector-submit" type="submit">Inspect</button>
</form>
<p id="analytics-inspector-status"></p><p id="analytics-inspector-evidence"></p>
<script type="module">
import { mountAnalyticsInspector } from "/analytics-inspector.js";
import { request } from "/api.js";
window.inspector = mountAnalyticsInspector(document, {
  getScope: () => ({
    category: "inverse", symbol: "BTCUSD", interval: "1",
    start: "2026-11-25T00:00", end: "2026-11-25T01:00",
  }),
  fetchCapabilities: (params, signal) =>
    request("/api/capabilities", params, { signal }),
});
</script>
</body></html>`;
const evidence = {
  schema_version: "1.0",
  data: {
    protocol_version: "tester.ui-capabilities/1",
    panels: {
      vwap: { state: "AVAILABLE", resource: "/api/analytics", schema_version: "1.0" },
    },
    execution_source_evidence: {
      status: "UNVERIFIED", historical_coverage: "UNKNOWN",
    },
    source_contracts: {
      public_executions: {
        quantity_unit: { inverse: "source_native_inverse_contract" },
      },
      observed_execution_metrics: {
        contract_version: "tester.execution-metrics/2",
        write_version: "tester.execution-metrics/2",
        pure_calculator: "src.analytics.research_metrics.calculate_v2",
        readback_state: "NOT_BOUND_TO_UI_ROUTE",
        quantity_unit: { inverse: "quote" },
      },
    },
  },
};
test("actual Chromium inspector renders UNKNOWN and ignores forged analytics route", async ({ page }) => {
  const calls = [];
  await page.route("http://terminal.test/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/capabilities") {
      calls.push(url.pathname);
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(evidence),
      });
    }
    if (url.pathname.endsWith(".js")) {
      return route.fulfill({
        contentType: "text/javascript",
        body: await readFile(path.join(process.cwd(), url.pathname.slice(1))),
      });
    }
    calls.push(url.pathname);
    return route.fulfill({ contentType: "text/html", body: html });
  });
  await page.goto("/");
  await page.locator("#analytics-inspector-submit").click();
  const status = page.locator("#analytics-inspector-status");
  await expect(status).toContainText("UNVERIFIED");
  await expect(status).toContainText("historical coverage UNKNOWN");
  await expect(status).toContainText("strategy admission DENIED");
  await expect(page.locator("#analytics-inspector-evidence")).toContainText(
    "NO analytics data request sent",
  );
  await page.selectOption("#analytics-inspector-metric", "hourly_range_percent");
  await page.locator("#analytics-inspector-submit").click();
  await expect(status).toContainText("analytics_quality_route_not_registered");
  expect(calls.filter((x) => x.startsWith("/api/"))).toEqual([
    "/api/capabilities", "/api/capabilities",
  ]);
});
