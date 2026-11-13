import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

const intervals = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];

test("real FastAPI capabilities and price-only routes across 13 UTC timeframes", async ({
  page,
}, testInfo) => {
  // No catalog interception: validate real API feature negotiation so an
  // optional, absent /api/market-catalog route cannot produce 404.

  const pageErrors = [], consoleErrors = [], serverErrors = [], forbiddenRequests = [];
  const networkEvidence = [];
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
    if (url.pathname.startsWith("/api/")) {
      networkEvidence.push({
        path: url.pathname,
        status: response.status(),
        interval: url.searchParams.get("interval"),
        candle_kind: url.searchParams.get("candle_kind"),
        schema_version: url.searchParams.get("schema_version"),
      });
      if ([404, 422, 503].includes(response.status()))
        serverErrors.push({ path: url.pathname, status: response.status() });
    }
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
  // The API is the actual application route, not a page mock. Check the
  // typed price-only response independently of canvas's Number geometry.
  const priceQuery = {
    category: "linear", symbol: "BTCUSDT", interval: "1",
    start_ms: String(Date.UTC(2024, 0, 1)),
    end_ms: String(Date.UTC(2024, 0, 2)),
    candle_kind: "official_mark", schema_version: "1.1",
  };
  const priceResponse = await page.request.get(
    "/api/price-candles?" + new URLSearchParams(priceQuery),
  );
  expect(priceResponse.status()).toBe(200);
  const priceBody = await priceResponse.json();
  expect(priceBody.schema_version).toBe("1.1");
  expect(priceBody.data[0].close).toBe("101.250000000000000000000001");
  expect(priceBody.data[0].timestamp_ms).toBe(Date.UTC(2024, 0, 1));
  expect(priceBody.data[0].end_ms).toBe(Date.UTC(2024, 0, 1, 0, 1));
  expect(priceBody.data[0].volume).toBeUndefined();
  await expect(mark.locator("details:not(.panel-evidence)")).toContainText(
    "101.250000000000000000000001",
  );
  // Route discovery remains protocol-qualified even when DATA #72 and
  // RESEARCH #71 are not composed into this independent UI branch.
  const discovery = await page.request.get(
    "/api/capabilities?" + new URLSearchParams({
      category: "linear", symbol: "BTCUSDT", interval: "1",
      start_ms: String(Date.UTC(2024, 0, 1)),
      end_ms: String(Date.UTC(2024, 0, 2)),
    }),
  );
  expect(discovery.status()).toBe(200);
  const contract = (await discovery.json()).data;
  expect(contract.protocol_version).toBe("tester.ui-capabilities/1");
  expect(contract.source_contracts.public_executions.contract_version)
    .toBe("tester-public-execution/1");
  expect(contract.source_contracts.observed_execution_metrics.contract_version)
    .toBe("tester.execution-metrics/1");
  expect(contract.source_contracts.public_auxiliary.readback_state)
    .toBe("NOT_BOUND_TO_UI_ROUTE");
  expect(contract.execution_source_evidence.status).toBe("UNVERIFIED");
  expect(contract.execution_source_evidence.contract_version)
    .toBe("tester-public-execution/1");
  expect(contract.execution_source_evidence.historical_coverage).toBe("UNKNOWN");
  expect(contract.execution_source_evidence.admitted_to_f).toBe(false);
  expect(contract.execution_source_evidence.evidence_revision).toBeUndefined();
  expect(contract.observed_page_revision.historical_dataset_revision)
    .toBe("UNPINNED");
  expect(contract.observed_page_revision.supports_f_admission).toBe(false);
  for (const panel of ["footprint", "delta", "cvd", "vwap", "poc", "profile"]) {
    expect(contract.panels[panel].status).toBe("UNAVAILABLE");
    expect(contract.panels[panel].source_contract_dependencies)
      .toEqual(["public_executions", "observed_execution_metrics"]);
    expect(contract.panels[panel].resource).toBeUndefined();
  }
  for (const panel of ["open_interest", "funding"]) {
    expect(contract.panels[panel].source_contract_dependencies)
      .toEqual(["public_auxiliary"]);
    expect(contract.panels[panel].resource).toBeUndefined();
  }
  for (const panel of ["mark", "index"]) {
    const source = contract.panels[panel];
    expect(source.resource).toBe("/api/price-candles");
    expect(source.schema_version).toBe("1.1");
    expect(source.revision_kind).toBe("observed_page_digest");
    expect(source.historical_dataset_revision).toBe("UNPINNED");
    expect(source.history_complete).toBe(false);
    expect(source.admitted_to_f).toBe(false);
  }
  await expect(page.locator('[data-panel="delta"] .panel-evidence'))
    .toContainText("tester-public-execution/1");
  expect(verified).toEqual(intervals);
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(serverErrors).toEqual([]);
  expect(forbiddenRequests).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("capability-13-tf.png"), fullPage: true });
  await writeFile(
    testInfo.outputPath("capability-network-evidence.json"),
    JSON.stringify({
      test_source: "CI_SYNTHETIC_NOT_AUTHENTIC_HISTORY",
      head_sha: process.env.TESTER_TESTED_HEAD_SHA || null,
      github_event_sha: process.env.GITHUB_SHA || null,
      pinned_producers: {
        data72: process.env.TESTER_DATA_SOURCE_SHA || null,
        research71: process.env.TESTER_RESEARCH_SOURCE_SHA || null,
      },
      tested_intervals: verified,
      network: networkEvidence,
      page_errors: pageErrors,
      console_errors: consoleErrors,
      unexpected_api_statuses: serverErrors,
      unsupported_requests: forbiddenRequests,
      exact_decimal_close: priceBody.data[0].close,
      capability_protocol: contract.protocol_version,
      source_contract_versions: {
        data: contract.source_contracts.public_executions.contract_version,
        research: contract.source_contracts.observed_execution_metrics.contract_version,
        public_auxiliary: contract.source_contracts.public_auxiliary.contract_version,
      },
      execution_source_evidence: contract.execution_source_evidence,
      history_complete: contract.observed_page_revision.history_complete,
      historical_dataset_revision:
        contract.observed_page_revision.historical_dataset_revision,
    }, null, 2),
  );
});
