// ROOT-ONLY opt-in, actual owned backend. No routes, response fixtures, server or downloads.
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { bucketStart, nextBoundary, shiftRange } from "../time.js";
import { adaptSeries } from "../series.js";
import { catalogMetric } from "../catalog-ui.js";
import { compareDecimal, PANELS } from "../model.js";
const INTERVALS = [
  "1",
  "3",
  "5",
  "15",
  "30",
  "60",
  "120",
  "240",
  "360",
  "720",
  "D",
  "W",
  "M",
];
if (process.env.TESTER_PC4_OPT_IN !== "pc4-owned")
  throw Error("pc4-owned opt-in required");
const raw = readFileSync(process.env.TESTER_PC4_CASE_FILE);
if (raw.length > 65536) throw Error("Case file exceeds 64KiB");
const suite = JSON.parse(raw.toString("utf8").replace(/^\uFEFF/, ""));
if (
  !/^[a-f0-9]{40}$/.test(suite.source_sha || "") ||
  suite.source_sha !== process.env.TESTER_PC4_SOURCE_SHA ||
  !/^[a-f0-9]{40}$/.test(process.env.TESTER_PC4_HARNESS_SHA || "") ||
  suite.symbol !== "BTCUSDT" ||
  !Array.isArray(suite.cases) ||
  suite.cases.length !== 13 ||
  new Set(suite.cases.map((c) => c.interval)).size !== 13
)
  throw Error("Exact source SHA, BTCUSDT and all 13 unique cases required");
for (const c of suite.cases)
  if (
    !INTERVALS.includes(c.interval) ||
    ![c.start_ms, c.end_ms].every(
      (t) => Number.isSafeInteger(t) && t >= 0 && t % 60000 === 0,
    ) ||
    c.start_ms >= c.end_ms ||
    c.end_ms - c.start_ms > 366 * 86400000 ||
    !Number.isInteger(c.records_expected_min) ||
    c.records_expected_min < 1 ||
    c.records_expected_min > 1000
  )
    throw Error("Invalid bounded minute-resolution case");
const input = (t) => new Date(t).toISOString().slice(0, 16);
const minute = (t) => Math.floor(Math.round(t) / 60000) * 60000;
async function range(page) {
  return Promise.all(
    ["#start", "#end"].map(async (s) =>
      Date.parse((await page.locator(s).inputValue()) + "Z"),
    ),
  );
}
const candleMatch = (response, c) => {
  const u = new URL(response.url());
  return (
    u.origin === "http://127.0.0.1:18767" &&
    u.pathname === "/api/candles" &&
    u.searchParams.get("symbol") === suite.symbol &&
    u.searchParams.get("interval") === c.interval &&
    Number(u.searchParams.get("start_ms")) === c.start_ms &&
    Number(u.searchParams.get("end_ms")) === c.end_ms
  );
};
async function settled(page) {
  await expect(page.locator("#chart-status")).toContainText(
    "panels have records",
  );
  await expect(
    page.locator("#panels > [data-panel] .panel-message", {
      hasText: "Loading",
    }),
  ).toHaveCount(0);
}
async function responseFor(page, c, action) {
  const response = page.waitForResponse((r) => candleMatch(r, c));
  await action();
  const result = await response;
  await settled(page);
  return result;
}
async function apply(page, c) {
  await page.locator("#download-options").evaluate((n) => (n.open = true));
  await page.locator("#start").fill(input(c.start_ms));
  await page.locator("#end").fill(input(c.end_ms));
  if ((await page.locator("#interval").inputValue()) !== c.interval) {
    await responseFor(page, c, () =>
      page.locator("#interval").selectOption(c.interval),
    );
  }
  return responseFor(page, c, () => page.locator("#apply").click());
}
async function panels(page) {
  return page.locator("#panels > [data-panel]").evaluateAll((nodes) =>
    nodes.map((n) => ({
      kind: n.dataset.panel,
      meta: n.querySelector(".panel-meta").textContent,
      message: n.querySelector(".panel-message").textContent,
      message_hidden: n.querySelector(".panel-message").hidden,
      canvas_hidden: n.querySelector(".canvas-wrap").hidden,
      canvas_dimensions: {
        width: n.querySelector("canvas").width,
        height: n.querySelector("canvas").height,
        css_width: n.querySelector("canvas").clientWidth,
        css_height: n.querySelector("canvas").clientHeight,
      },
      evidence: n.querySelector(".panel-evidence pre").textContent,
      page: n.querySelector(".panel-paging").textContent,
      exact_rows: n.querySelectorAll("details:not(.panel-evidence) table tr")
        .length,
    })),
  );
}
async function painted(page, kind) {
  const canvas = page.locator(`[data-panel="${kind}"] canvas`);
  await canvas.scrollIntoViewIfNeeded();
  await expect(canvas).toBeVisible();
  return canvas.evaluate((c) => {
    const image = c.getContext("2d").getImageData(0, 0, c.width, c.height);
    let marketPixels = 0;
    for (let i = 0; i < image.data.length; i += 4) {
      const [r, g, b, a] = image.data.slice(i, i + 4);
      if (
        a &&
        ((Math.abs(r - 97) < 5 &&
          Math.abs(g - 213) < 5 &&
          Math.abs(b - 191) < 5) ||
          (Math.abs(r - 246) < 5 &&
            Math.abs(g - 132) < 5 &&
            Math.abs(b - 138) < 5))
      )
        marketPixels++;
    }
    return {
      width: c.width,
      height: c.height,
      css_width: c.clientWidth,
      css_height: c.clientHeight,
      market_pixels: marketPixels,
    };
  });
}
for (const c of suite.cases)
  test(`PC4 actual ${suite.symbol} timeframe ${c.interval}`, async ({
    page,
  }, testInfo) => {
    const report = {
      classification: "ROOT_ONLY_ACTUAL_BACKEND_NO_ROUTE_MOCK",
      application_source_sha: suite.source_sha,
      expected_device_scale_factor: Number(
        process.env.TESTER_PC4_DEVICE_SCALE_FACTOR ?? "1",
      ),
      harness_source_sha: process.env.TESTER_PC4_HARNESS_SHA,
      source_identity:
        "operator supplied immutable release pin; this harness does not attest process identity",
      case_file_sha256: createHash("sha256").update(raw).digest("hex"),
      case: c,
      symbol: suite.symbol,
      started_utc: new Date().toISOString(),
      page_errors: [],
      console_errors: [],
      request_failures: [],
      api: [],
      panels: [],
      gestures: [],
    };
    let assertionsCompleted = false;
    const pending = [];
    const starts = new Map();
    page.on("pageerror", (e) => report.page_errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") report.console_errors.push(m.text());
    });
    page.on("request", (r) => {
      starts.set(r, Date.now());
      if (r.method() !== "GET")
        report.request_failures.push({
          method: r.method(),
          url: r.url(),
          reason: "unexpected non-GET: harness never submits work",
        });
    });
    page.on("requestfailed", (r) => {
      if (r.failure()?.errorText !== "net::ERR_ABORTED")
        report.request_failures.push({
          url: r.url(),
          reason: r.failure()?.errorText,
        });
    });
    page.on("response", (response) => {
      const u = new URL(response.url());
      if (
        u.origin !== "http://127.0.0.1:18767" ||
        !u.pathname.startsWith("/api/")
      )
        return;
      const entry = {
        path: u.pathname,
        query: Object.fromEntries(u.searchParams),
        status: response.status(),
        elapsed_ms: Date.now() - (starts.get(response.request()) || Date.now()),
      };
      report.api.push(entry);
      pending.push(
        (async () => {
          try {
            const body = await response.text();
            entry.response_bytes = Buffer.byteLength(body);
            entry.response_sha256 = createHash("sha256")
              .update(body)
              .digest("hex");
            const p = JSON.parse(body);
            entry.schema_version = p.schema_version;
            entry.records = Array.isArray(p.data) ? p.data.length : null;
            entry.availability = p.availability;
            entry.coverage = p.coverage;
            entry.error_code = p.error?.code;
          } catch {
            entry.body_read = "unavailable_or_aborted";
          }
        })(),
      );
    });
    try {
      await page.goto("/");
      report.device_pixel_ratio = await page.evaluate(
        () => window.devicePixelRatio,
      );
      expect(report.device_pixel_ratio).toBe(
        report.expected_device_scale_factor,
      );
      await expect(page.locator("[data-row]").first()).toBeVisible();
      const sortResponse = page.waitForResponse((r) => {
        const u = new URL(r.url());
        return (
          u.pathname === "/api/market-catalog/linear" &&
          u.searchParams.get("sort") === "price" &&
          u.searchParams.get("direction") === "asc"
        );
      });
      await page
        .getByRole("button", { name: "Sort Price", exact: true })
        .click();
      const sorted = await sortResponse;
      expect(sorted.ok()).toBeTruthy();
      const catalog = (await sorted.json()).data;
      let absent = false,
        prior = null;
      for (const row of catalog.rows) {
        const v = catalogMetric(row.metrics?.price).value;
        if (v === null) {
          absent = true;
          continue;
        }
        expect(absent, "render-unavailable values must rank last").toBe(false);
        if (prior !== null)
          expect(compareDecimal(prior, v)).toBeLessThanOrEqual(0);
        prior = v;
      }
      report.catalog = {
        revision: catalog.revision,
        total: catalog.total,
        rendered: catalog.rows.length,
        sort: "price",
        direction: "asc",
      };
      await page.getByLabel("Search symbols").fill(suite.symbol);
      const row = page.locator(`[data-row="linear:${suite.symbol}"]`);
      await expect(row).toBeVisible();
      await page.getByLabel(`Select ${suite.symbol}`, { exact: true }).check();
      await page.reload();
      await expect(page.getByLabel("Search symbols")).toHaveValue(suite.symbol);
      await expect(
        page.getByLabel(`Select ${suite.symbol}`, { exact: true }),
      ).toBeChecked();
      await expect(page.locator('th[aria-sort="ascending"]')).toContainText(
        "Price",
      );
      await page.locator("#download-options").evaluate((n) => (n.open = true));
      await expect(page.locator("#interval option")).toHaveCount(13);
      await page.locator("#start").fill(input(c.start_ms));
      await page.locator("#end").fill(input(c.end_ms));
      await page.locator("#interval").selectOption(c.interval);
      const sourceResponse = await responseFor(page, c, () =>
        page
          .getByRole("button", { name: `Chart ${suite.symbol}`, exact: true })
          .click(),
      );
      expect(sourceResponse.ok()).toBeTruthy();
      const payload = await (await apply(page, c)).json();
      expect(payload.schema_version).toBe("1.0");
      expect(payload.data.length).toBeGreaterThanOrEqual(
        c.records_expected_min,
      );
      const q = {
        category: "linear",
        symbol: suite.symbol,
        interval: c.interval,
        start_ms: c.start_ms,
        end_ms: c.end_ms,
      };
      const series = adaptSeries(payload, "candles", q);
      expect(series.renderable).toBe(true);
      expect(series.rows.length).toBeGreaterThanOrEqual(c.records_expected_min);
      report.candles = {
        rows: payload.data,
        availability: series.availability,
        coverage: series.coverage,
        provenance: series.provenance,
      };
      for (const bar of series.rows) {
        expect(bar.ts).toBeGreaterThanOrEqual(c.start_ms);
        expect(bar.ts).toBeLessThan(c.end_ms);
        expect(bucketStart(bar.ts, c.interval)).toBe(bar.ts);
        expect(nextBoundary(bar.ts, c.interval)).toBeLessThanOrEqual(c.end_ms);
      }
      report.panels = await panels(page);
      expect(report.panels).toHaveLength(PANELS.length);
      for (const panel of report.panels) {
        if (panel.canvas_hidden) {
          expect(panel.message).toContain("DATA_UNAVAILABLE");
          expect(panel.message_hidden).toBe(false);
        } else expect(panel.meta).not.toBe("");
      }
      report.candle_canvas = await painted(page, "candles");
      report.volume_canvas = await painted(page, "volume");
      for (const dimensions of [report.candle_canvas, report.volume_canvas]) {
        expect(dimensions.css_width).toBeGreaterThan(0);
        expect(dimensions.css_height).toBeGreaterThan(0);
        expect(dimensions.width).toBe(
          dimensions.css_width * report.device_pixel_ratio,
        );
        expect(dimensions.height).toBe(
          dimensions.css_height * report.device_pixel_ratio,
        );
      }
      expect(report.candle_canvas.market_pixels).toBeGreaterThan(0);
      expect(report.volume_canvas.market_pixels).toBeGreaterThan(0);
      await page.locator("#download-options").evaluate((n) => (n.open = false));
      await page.locator("#terminal").screenshot({
        path: testInfo.outputPath(`timeframe-${c.interval}-actual.png`),
      });
      await testInfo.attach(`timeframe-${c.interval}-actual`, {
        path: testInfo.outputPath(`timeframe-${c.interval}-actual.png`),
        contentType: "image/png",
      });
      async function gesture(name, action, expected) {
        const before = await range(page);
        const wait = page.waitForResponse((r) => {
          const u = new URL(r.url());
          return (
            u.pathname === "/api/candles" &&
            u.searchParams.get("interval") === c.interval &&
            (Number(u.searchParams.get("start_ms")) !== before[0] ||
              Number(u.searchParams.get("end_ms")) !== before[1])
          );
        });
        await action();
        const result = await wait;
        await settled(page);
        const after = await range(page);
        expect(after[1]).toBeGreaterThan(after[0]);
        if (expected) expect(after).toEqual(expected);
        report.gestures.push({
          name,
          before,
          after,
          status: result.status(),
          panels: await panels(page),
        });
      }
      const canvas = page.locator('[data-panel="candles"] canvas');
      await canvas.scrollIntoViewIfNeeded();
      let box = await canvas.boundingBox();
      await page.mouse.move(
        box.x + 8 + (box.width - 56) * 0.37,
        box.y + box.height / 2,
      );
      await expect(page.locator("#crosshair")).not.toHaveText("—");
      report.crosshair = await page.locator("#crosshair").textContent();
      await gesture("wheel_zoom", () => page.mouse.wheel(0, -120));
      let zoom = await range(page);
      expect(zoom[1] - zoom[0]).toBeLessThan(c.end_ms - c.start_ms);
      await apply(page, c);
      await canvas.scrollIntoViewIfNeeded();
      box = await canvas.boundingBox();
      const point = { x: box.x + box.width * 0.55, y: box.y + box.height / 2 };
      await page.mouse.move(point.x, point.y);
      await page.mouse.down();
      await page.mouse.move(point.x + 50, point.y, { steps: 5 });
      await gesture("pointer_pan", () => page.mouse.up());
      const moved = await range(page);
      expect(moved[1] - moved[0]).toBe(c.end_ms - c.start_ms);
      await apply(page, c);
      const before = await range(page);
      const expected = ["W", "M"].includes(c.interval)
        ? shiftRange(before, c.interval, 1).map(minute)
        : before.map((t) => minute(t + (before[1] - before[0]) * 0.5));
      await gesture(
        "navigation_later",
        () =>
          page.getByRole("button", { name: "Pan later", exact: true }).click(),
        expected,
      );
      await apply(page, c);
      report.restored_panels = await panels(page);
      for (const snapshot of [
        report.panels,
        report.restored_panels,
        ...report.gestures.map((g) => g.panels),
      ])
        for (const panel of snapshot)
          if (!panel.canvas_hidden) {
            const dimensions = panel.canvas_dimensions;
            expect(dimensions.css_width).toBeGreaterThan(0);
            expect(dimensions.css_height).toBeGreaterThan(0);
            expect(dimensions.width).toBe(
              dimensions.css_width * report.device_pixel_ratio,
            );
            expect(dimensions.height).toBe(
              dimensions.css_height * report.device_pixel_ratio,
            );
          }
      expect(report.page_errors).toEqual([]);
      expect(report.request_failures).toEqual([]);
      assertionsCompleted = true;
    } finally {
      await Promise.allSettled(pending);
      report.finished_utc = new Date().toISOString();
      report.duration_ms =
        Date.parse(report.finished_utc) - Date.parse(report.started_utc);
      report.assertions_completed = assertionsCompleted;
      report.final_panels = await panels(page).catch(() => []);
      report.api_metrics = {
        count: report.api.length,
        total_response_bytes: report.api.reduce(
          (n, r) => n + (r.response_bytes || 0),
          0,
        ),
        max_elapsed_ms: Math.max(0, ...report.api.map((r) => r.elapsed_ms)),
        http_errors: report.api.filter((r) => r.status >= 400).length,
      };
      await testInfo.attach(`pc4-timeframe-${c.interval}-evidence`, {
        body: JSON.stringify(report, null, 2),
        contentType: "application/json",
      });
    }
  });
