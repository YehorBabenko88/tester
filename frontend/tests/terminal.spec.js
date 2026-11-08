import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { candle, envelope } from "./fixtures/canonical.js";
const start = 1704067200000;
const m = (value) => ({
  value,
  source: "Bybit REST",
  as_of: start,
  availability: "AVAILABLE",
  unit: "USD",
});
const instruments = [
  {
    category: "linear",
    symbol: "BTCUSDT",
    metrics: { price: m("42000.01"), volatility: m("2.4") },
  },
  {
    category: "linear",
    symbol: "ETHUSDT",
    metrics: { price: m("2200.1"), volatility: m("3.5") },
  },
];
async function setup(page, options = {}) {
  await page.route("http://terminal.test/**", async (route) => {
    const u = new URL(route.request().url());
    if (options.pagePayload && u.pathname.startsWith("/api/")) {
      const value = options.pagePayload(u);
      if (value) {
        await route.fulfill({ json: value });
        return;
      }
    }
    let data = {};
    if (!u.pathname.startsWith("/api/")) {
      const file = path.join(
        process.cwd(),
        u.pathname === "/" ? "index.html" : u.pathname.slice(1),
      );
      await route.fulfill({
        body: await readFile(file),
        contentType: file.endsWith(".js")
          ? "text/javascript"
          : file.endsWith(".css")
            ? "text/css"
            : "text/html",
      });
      return;
    }
    if (u.pathname === "/api/sources")
      data = [
        {
          id: "bybit_rest",
          availability: "available",
          kinds: ["candles", "trades"],
          provenance: "Bybit",
        },
      ];
    if (u.pathname.startsWith("/api/market-catalog/")) {
      let rows = (options.instruments || instruments).filter((x) =>
        x.symbol.includes((u.searchParams.get("search") || "").toUpperCase()),
      );
      const saved = u.searchParams.getAll("watchlist");
      if (saved.length)
        rows = rows.filter((x) => saved.includes(`${x.category}:${x.symbol}`));
      const offset = Number(u.searchParams.get("offset") || 0),
        limit = Number(u.searchParams.get("limit") || 50);
      data = {
        rows: rows.slice(offset, offset + limit),
        total: rows.length,
        revision: "fixture-1",
        catalog: {
          loaded: rows.length,
          completeness: "EXHAUSTED",
          state: "CURRENT",
          next_refresh_at: Date.now() / 1000 + 300,
        },
      };
    }
    if (u.pathname === "/api/downloads")
      data =
        route.request().method() === "POST"
          ? { id: "j1", state: "DISCOVERING" }
          : { jobs: options.jobs || [] };
    if (u.pathname.startsWith("/api/downloads/")) {
      if (options.onJob) await options.onJob();
      data = options.jobs?.[0] || { id: "j1", state: "DOWNLOADING" };
    }
    if (u.pathname === "/api/quality")
      data = {
        availability: "PARTIAL",
        reason: "Archive not verified",
        gaps: [{ start_ms: start, end_ms: start + 60000 }],
        raw: true,
      };
    if (
      [
        "/api/candles",
        "/api/footprint",
        "/api/open-interest",
        "/api/funding",
        "/api/analytics",
      ].includes(u.pathname)
    )
      data = {
        availability: "DATA_UNAVAILABLE",
        reason: "Historical trades not ingested",
        source: "C aggregates",
        as_of: start,
        rows: [],
      };
    if (u.pathname === "/api/candles" && options.candles)
      data = options.candles;
    if (u.pathname === "/api/footprint" && options.footprint)
      data = options.footprint;
    if (u.pathname === "/api/open-interest" && options.oi) data = options.oi;
    if (u.pathname === "/api/funding" && options.funding)
      data = options.funding;
    if (u.pathname === "/api/analytics" && options.analytics)
      data = options.analytics(u.searchParams.get("metric"));
    if (u.pathname === "/api/coverage")
      data = [
        {
          status: "partial",
          kind: "candles",
          start_ms: start,
          end_ms: start + 60000,
          provenance: "Archive not verified",
        },
      ];
    if (options.apiError && u.pathname.startsWith("/api/market-catalog/")) {
      await route.fulfill({
        status: 503,
        json: {
          error: { code: "provider_unavailable", message: "DB offline" },
        },
      });
      return;
    }
    await route.fulfill({
      json:
        u.pathname === "/api/candles" && options.envelope
          ? options.envelope
          : { schema_version: "1.0", data, next_cursor: null },
    });
  });
  if (options.jobs)
    await page.addInitScript(() =>
      localStorage.setItem("bybit-jobs", '["j1"]'),
    );
  await page.goto("/");
  await page.locator("#download-options").evaluate((e) => (e.open = true));
  if (options.jobs)
    await page.locator("#downloads").evaluate((e) => (e.open = true));
}
test("multi-selection survives search and reload, saves exact identities", async ({
  page,
}) => {
  await setup(page);
  await page.getByLabel("Select BTCUSDT").check();
  await page.getByLabel("Select ETHUSDT").check();
  await page.getByLabel("Search symbols").fill("BTC");
  await expect(page.getByTestId("selection-count")).toHaveText("2 selected");
  await page.getByRole("button", { name: "Save watchlist" }).click();
  await page.reload();
  await expect(page.getByTestId("selection-count")).toHaveText("2 selected");
});
test("bounded opaque cursor pages preserve equal timestamp footprint levels beyond 1000", async ({
  page,
}) => {
  const calls = [];
  await setup(page, {
    pagePayload: (u) => {
      if (u.pathname !== "/api/footprint") return null;
      calls.push(u);
      const cursor = u.searchParams.get("cursor");
      return {
        schema_version: "1.0",
        next_cursor: cursor ? null : "opaque:1000:same-time",
        data: cursor
          ? [
              {
                timestamp_ms: start + 999 * 60000,
                price: "101",
                buy_volume: "2",
                sell_volume: "1",
                provenance: "fixture://levels",
              },
            ]
          : Array.from({ length: 1000 }, (_, i) => ({
              timestamp_ms: start + i * 60000,
              price: "100",
              buy_volume: "1",
              sell_volume: "0",
              provenance: "fixture://levels",
            })),
      };
    },
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2025-01-01T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  const panel = page.locator('[data-panel="footprint"]');
  await expect(
    panel.getByRole("button", { name: "Next data page" }),
  ).toBeEnabled();
  expect(calls.length).toBe(1);
  await panel.getByRole("button", { name: "Next data page" }).click();
  await expect(panel).toContainText("Page 2");
  await expect(panel.locator("details:not(.panel-evidence)")).toContainText(
    "101",
  );
  expect(calls[1].searchParams.get("cursor")).toBe("opaque:1000:same-time");
  expect(
    calls.every((u) => u.searchParams.get("limit") === "1000"),
  ).toBeTruthy();
  expect(calls[0].searchParams.get("start_ms")).toBe(
    calls[1].searchParams.get("start_ms"),
  );
  await panel.getByRole("button", { name: "Previous data page" }).click();
  await expect(panel).toContainText("Page 1");
});
for (const status of ["UNAVAILABLE", "UNVERIFIED"])
  test(`full loadCharts suppresses canonical ${status} and retains evidence`, async ({
    page,
  }) => {
    await setup(page, { envelope: envelope([candle(status)], status) });
    await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
    await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
    await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
    const panel = page.locator('[data-panel="candles"]');
    await expect(panel).toContainText("source_conflict_fixture");
    await expect(panel).toContainText(status);
    await expect(panel).toContainText("fixture://source");
    await expect(panel.locator("canvas")).toBeHidden();
    await expect(panel).not.toContainText("[object Object]");
  });
test("canonical PARTIAL retains nested reason and record coverage", async ({
  page,
}) => {
  await setup(page, {
    envelope: envelope(
      [candle("PARTIAL", "missing_interval_fixture")],
      "PARTIAL",
      "missing_interval_fixture",
    ),
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  const panel = page.locator('[data-panel="candles"]');
  await expect(panel).toContainText("missing_interval_fixture");
  await expect(panel).toContainText("fixture://coverage");
  await expect(panel.locator("canvas")).toBeVisible();
});
for (const status of ["PARTIAL", "UNKNOWN", "ERROR"])
  test(`full loadCharts cannot display COMPLETE over row coverage ${status}`, async ({
    page,
  }) => {
    const row = candle("PARTIAL", "row_coverage_fixture");
    row.coverage.status = status;
    row.coverage.evidence_refs = ["fixture://weak-row-" + status];
    const payload = envelope([row], "AVAILABLE");
    payload.coverage.status = "COMPLETE";
    await setup(page, { envelope: payload });
    await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
    await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
    await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
    const panel = page.locator('[data-panel="candles"]');
    await expect(panel.locator(".panel-meta")).toContainText(
      "completeness " + status,
    );
    await expect(panel.locator(".panel-meta")).not.toContainText(
      "completeness COMPLETE",
    );
    await expect(panel).toContainText("row_coverage_fixture");
    await expect(panel.locator(".panel-evidence")).toContainText(
      "fixture://weak-row-" + status,
    );
    await expect(panel.locator(".panel-evidence")).toContainText("COMPLETE");
  });
test("partial candles show provenance, closure and synchronized time ranges", async ({
  page,
}) => {
  await setup(page, {
    candles: {
      availability: "PARTIAL",
      source: "Bybit archive",
      as_of: start,
      completeness: "PARTIAL",
      reason: "Missing interval",
      rows: [
        {
          ts: start,
          open: "1",
          high: "3",
          low: "1",
          close: "2",
          volume: "5",
          is_closed: false,
        },
      ],
    },
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  await expect(page.locator('[data-panel="candles"]')).toContainText(
    "unclosed 1",
  );
  await expect(page.locator('[data-panel="volume"]')).toContainText("PARTIAL");
  const requests = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/")) requests.push(new URL(r.url()));
  });
  await page.getByLabel("Zoom in", { exact: true }).click();
  await expect
    .poll(() => requests.filter((u) => u.pathname === "/api/candles").length)
    .toBeGreaterThan(0);
  const rr = requests.filter((u) =>
    [
      "/api/candles",
      "/api/open-interest",
      "/api/footprint",
      "/api/funding",
      "/api/analytics",
    ].includes(u.pathname),
  );
  expect(new Set(rr.map((u) => u.searchParams.get("start_ms"))).size).toBe(1);
  expect(new Set(rr.map((u) => u.searchParams.get("end_ms"))).size).toBe(1);
  expect(rr.every((u) => u.searchParams.get("limit") === "1000")).toBeTruthy();
  expect(requests.some((u) => u.pathname === "/api/trades")).toBeFalsy();
});
test("large screener paginates bounded rows", async ({ page }) => {
  await setup(page, {
    instruments: Array.from({ length: 1000 }, (_, i) => ({
      category: "linear",
      symbol: "COIN" + i,
      metrics: {},
    })),
  });
  await expect(page.locator("[data-row]")).toHaveCount(50);
  const initial = await page
    .locator("[data-row]")
    .first()
    .getAttribute("data-row");
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect
    .poll(() => page.locator("[data-row]").first().getAttribute("data-row"))
    .not.toBe(initial);
  expect(await page.locator("[data-row]").count()).toBeLessThanOrEqual(50);
});
test("saved-only filter queries global favorites after pagination", async ({
  page,
}) => {
  await setup(page, {
    instruments: Array.from({ length: 1000 }, (_, i) => ({
      category: "linear",
      symbol: "COIN" + i,
      metrics: {},
    })),
  });
  const first = page.locator("[data-row]").first();
  const label = await first.getAttribute("data-row");
  await first.locator("input").check();
  await page.getByRole("button", { name: "Save watchlist" }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect
    .poll(() => page.locator("[data-row]").first().getAttribute("data-row"))
    .not.toBe(label);
  await page.getByLabel("Saved only").check();
  await expect(page.locator("[data-row]")).toHaveCount(1);
});
test("slow job requests never create overlapping polling waves", async ({
  page,
}) => {
  let calls = 0,
    release;
  const wait = new Promise((resolve) => (release = resolve));
  await page.clock.install();
  await setup(page, {
    jobs: [{ id: "j1", state: "DOWNLOADING", progress: 0 }],
    onJob: async () => {
      calls++;
      await wait;
    },
  });
  await expect.poll(() => calls).toBe(1);
  await page.clock.runFor(8000);
  expect(calls).toBe(1);
  release();
});
test("polls do not start for a job while retry is pending", async ({
  page,
}) => {
  await page.clock.install();
  await setup(page, { jobs: [{ id: "j1", state: "FAILED", progress: 0 }] });
  await expect(page.getByRole("button", { name: "Retry j1" })).toBeEnabled();
  let gets = 0,
    release;
  const pending = new Promise((resolve) => (release = resolve));
  await page.route("**/api/downloads/j1/retry", async (route) => {
    await pending;
    await route.fulfill({
      json: {
        schema_version: "1.0",
        data: { id: "j1", state: "DOWNLOADING", progress: 0 },
      },
    });
  });
  await page.route("**/api/downloads/j1", async (route) => {
    gets++;
    await route.fulfill({
      json: {
        schema_version: "1.0",
        data: { id: "j1", state: "FAILED", progress: 0 },
      },
    });
  });
  await page.getByRole("button", { name: "Retry j1" }).click();
  await page.getByRole("button", { name: "Refresh jobs" }).click();
  await page.clock.runFor(3000);
  expect(gets).toBe(0);
  release();
});
test("download creates per-instrument jobs with exclusive UTC range", async ({
  page,
}) => {
  await setup(page);
  await page.getByLabel("Select BTCUSDT").check();
  await page.getByLabel("Select ETHUSDT").check();
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
  const bodies = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/downloads")
      bodies.push(r.postDataJSON());
  });
  await page.getByRole("button", { name: "Download selected" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies.map((b) => b.symbol).sort()).toEqual(["BTCUSDT", "ETHUSDT"]);
  expect(
    bodies.every((b) => b.start_ms === start && b.end_ms === start + 86400000),
  ).toBeTruthy();
});
test("all derived panels render server strings, profile accepts many prices at one time", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await setup(page, {
    footprint: [
      {
        timestamp_ms: start,
        price: "2",
        buy_volume: "3",
        sell_volume: "1",
        provenance: "C from verified trades",
      },
    ],
    analytics: (kind) => ({
      availability: "PARTIAL",
      source: "C",
      reason: "Coverage partial",
      rows:
        kind === "profile"
          ? [
              { ts: start, price: "1", volume: "3", is_poc: true },
              { ts: start, price: "2", volume: "2" },
            ]
          : [{ ts: start, value: "1.00000000000000000001" }],
    }),
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  for (const kind of [
    "footprint",
    "delta",
    "cvd",
    "profile",
    "poc",
    "vwap",
    "mark",
    "index",
  ])
    await expect(page.locator(`[data-panel="${kind}"] canvas`)).toBeVisible();
  expect(errors).toEqual([]);
});
test("sort is requested on server and unavailable panels explain why", async ({
  page,
}) => {
  await setup(page);
  const req = page.waitForRequest(
    (r) =>
      r.url().includes("/api/market-catalog/") &&
      r.url().includes("sort=volatility"),
  );
  await page
    .getByRole("button", { name: "Sort Volatility", exact: true })
    .click();
  await req;
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  await expect(page.locator('[data-panel="footprint"]')).toContainText(
    "DATA_UNAVAILABLE",
  );
  await expect(page.locator('[data-panel="footprint"]')).toContainText(
    "Historical trades not ingested",
  );
});
test("API outage is visible and retry works without fake rows", async ({
  page,
}) => {
  await setup(page, { apiError: true });
  await expect(page.getByTestId("screener-status")).toContainText(
    "provider_unavailable",
  );
  await expect(
    page.getByRole("button", { name: "Refresh now / Retry instruments" }),
  ).toBeVisible();
  await expect(page.locator("[data-row]")).toHaveCount(0);
});
test("unknown progress and per-source partial failure support retry", async ({
  page,
}) => {
  await setup(page, {
    jobs: [
      {
        id: "j1",
        symbol: "BTCUSDT",
        state: "PARTIAL",
        progress: { completed: 10 },
        sources: [
          {
            source: "archive",
            state: "FAILED",
            error: "Checksum mismatch",
            bytes: 1024,
            verified_records: 0,
          },
        ],
      },
    ],
  });
  await expect(page.locator("#jobs")).toContainText("Checksum mismatch");
  await expect(page.locator("#jobs")).toContainText("Unknown total");
  await expect(page.locator("#jobs")).not.toContainText("100%");
  const req = page.waitForRequest("**/api/downloads/j1/retry");
  await page.getByRole("button", { name: "Retry j1" }).click();
  await req;
});
test("corrupt candles are explicit and long range stays bounded", async ({
  page,
}) => {
  await setup(page, {
    candles: {
      availability: "AVAILABLE",
      source: "Bybit",
      as_of: start,
      rows: [{ ts: start, open: "9", high: "2", low: "1", close: "2" }],
    },
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2025-01-01T00:00");
  const req = page.waitForRequest(
    (r) => r.url().includes("/api/candles") && r.url().includes("limit=1000"),
  );
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  await req;
  await expect(page.locator('[data-panel="candles"]')).toContainText(
    "DATA_UNAVAILABLE",
  );
  await expect(page.locator('[data-panel="candles"]')).toContainText("Corrupt");
});
test("desktop and narrow layouts contain page content without horizontal overflow", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await setup(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  if (process.env.UI_SCREENSHOT) {
    await page
      .locator(".eyebrow")
      .evaluate((e) => (e.textContent = "TEST FIXTURE · NOT LIVE MARKET DATA"));
    await page.screenshot({ path: process.env.UI_SCREENSHOT, fullPage: true });
  }
  await page.setViewportSize({ width: 650, height: 900 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
test("E v1 raw arrays render OHLCV, OI and funding without claiming complete coverage", async ({
  page,
}) => {
  await setup(page, {
    candles: [
      {
        timestamp_ms: start,
        open: "1",
        high: "3",
        low: "1",
        close: "2",
        volume: "5",
        provenance: "Bybit REST",
      },
    ],
    oi: [{ timestamp_ms: start, value: "123.45", provenance: "Bybit REST" }],
    funding: [
      { timestamp_ms: start, rate: "0.0001", provenance: "Bybit REST" },
    ],
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-01-02T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  for (const kind of ["candles", "volume", "open_interest", "funding"]) {
    await expect(page.locator(`[data-panel="${kind}"] canvas`)).toBeVisible();
    await expect(page.locator(`[data-panel="${kind}"]`)).toContainText(
      "PARTIAL",
    );
  }
  await expect(page.locator('[data-panel="candles"]')).toContainText(
    "closure unknown 1",
  );
});
test("monthly keyboard navigation uses UTC calendar edges and resize respects high DPI", async ({
  browser,
}) => {
  const context = await browser.newContext({
    deviceScaleFactor: 2,
    baseURL: "http://terminal.test",
  });
  const page = await context.newPage();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, {
    pagePayload: (u) =>
      u.pathname === "/api/candles"
        ? {
            schema_version: "1.0",
            next_cursor: null,
            data: [
              {
                timestamp_ms: Date.UTC(2024, 0, 1),
                open: "1",
                high: "2",
                low: "1",
                close: "2",
                volume: "3",
                provenance: "fixture://calendar",
              },
            ],
          }
        : null,
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-02-01T00:00");
  await page.locator("#interval").selectOption("M");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  const canvas = page.locator('[data-panel="candles"] canvas');
  await expect(canvas).toBeVisible();
  await expect
    .poll(() =>
      canvas.evaluate(
        (c) => Math.abs(c.width - c.clientWidth * devicePixelRatio) < 2,
      ),
    )
    .toBe(true);
  await page
    .locator('[data-panel="candles"] .canvas-wrap')
    .evaluate((e) => (e.style.height = "350px"));
  await expect.poll(() => canvas.evaluate((c) => c.height)).toBe(700);
  await canvas.focus();
  await canvas.press("ArrowRight");
  await expect(page.getByLabel("Range start UTC")).toHaveValue(
    "2024-02-01T00:00",
  );
  await expect(page.getByLabel("Range end UTC")).toHaveValue(
    "2024-03-01T00:00",
  );
  await context.close();
});
test("strategy workspace fails closed before validated F outputs exist", async ({
  page,
}) => {
  await setup(page);
  for (const name of [
    "Strategy preview",
    "Stop Loss / Take Profit",
    "Simulation journal",
    "Equity",
    "Drawdown",
    "Trade overlays",
  ]) {
    const panel = page.getByRole("region", { name, exact: true });
    await expect(panel).toContainText("DATA_UNAVAILABLE");
    await expect(panel).toContainText("validated simulation");
  }
});
test("editing range fields invalidates all old chart cursors before paging", async ({
  page,
}) => {
  const requests = [];
  await setup(page, {
    pagePayload: (u) => {
      if (u.pathname !== "/api/candles") return null;
      requests.push(u);
      return {
        schema_version: "1.0",
        next_cursor: "old-query-token",
        data: [
          {
            timestamp_ms: start,
            open: "1",
            high: "2",
            low: "1",
            close: "2",
            volume: "3",
            provenance: "fixture://cursor",
          },
        ],
      };
    },
  });
  await page.getByLabel("Range start UTC").fill("2024-01-01T00:00");
  await page.getByLabel("Range end UTC").fill("2024-02-01T00:00");
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  const panel = page.locator('[data-panel="candles"]');
  await expect(
    panel.getByRole("button", { name: "Next data page" }),
  ).toBeEnabled();
  await page.getByLabel("Range end UTC").fill("2024-03-01T00:00");
  await panel.getByRole("button", { name: "Next data page" }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(1);
  expect(requests.at(-1).searchParams.get("cursor")).toBeNull();
  await expect(panel).toContainText("Page 1");
});
test("funding event and selected OI source cadence stay separate from display timeframe", async ({
  page,
}) => {
  const requests = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/")) requests.push(new URL(r.url()));
  });
  await setup(page);
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  await expect
    .poll(() => requests.some((u) => u.pathname === "/api/funding"))
    .toBe(true);
  await page.locator("#interval").selectOption("D");
  await page.getByLabel("OI source cadence").selectOption("1h");
  await expect
    .poll(() =>
      requests
        .filter((u) => u.pathname === "/api/open-interest")
        .at(-1)
        ?.searchParams.get("source_interval"),
    )
    .toBe("1h");
  const funding = requests.filter((u) => u.pathname === "/api/funding");
  expect(
    funding.every((u) => u.searchParams.get("source_interval") === "event"),
  ).toBe(true);
  expect(funding.at(-1).searchParams.get("interval")).toBe("D");
  const oi = requests.filter((u) => u.pathname === "/api/open-interest");
  expect(oi[0].searchParams.get("source_interval")).toBe("5min");
  expect(oi.at(-1).searchParams.get("interval")).toBe("D");
  expect(requests.some((u) => u.pathname === "/api/trades")).toBe(false);
});

test("complete downloads retain UNKNOWN coverage and measured telemetry", async ({
  page,
}) => {
  await setup(page, {
    jobs: [
      {
        id: "j1",
        symbol: "BTCUSDT",
        state: "completed",
        checkpoint: {
          telemetry: {
            schema_version: 1,
            bytes_received: 8192,
            records_ingested: 10,
            records_verified: 10,
            elapsed_seconds: 2,
            bytes_per_second: 4096,
            source: "bybit",
            coverage: "UNKNOWN",
            work_complete: true,
            expected_records: null,
            expected_bytes: null,
            evidence_refs: ["batch:1"],
          },
        },
      },
    ],
  });
  await expect(page.locator("#jobs")).toContainText("Download COMPLETE");
  await expect(page.locator("#jobs")).toContainText("Coverage UNKNOWN");
  await expect(page.locator("#jobs")).toContainText("Records verified: 10");
  await expect(page.locator("#jobs")).toContainText("Bytes received: 8192");
  await expect(page.locator("#jobs progress").first()).not.toHaveAttribute(
    "value",
  );
  await expect(page.locator("#overall")).toContainText(
    "1/1 downloads complete",
  );
  await expect(page.locator("#overall")).not.toContainText("verified complete");
});

test("legacy completed checkpoints do not fabricate percentages or coverage", async ({
  page,
}) => {
  await setup(page, {
    jobs: [
      {
        id: "j1",
        symbol: "BTCUSDT",
        state: "completed",
        progress: 1,
        verified: true,
      },
    ],
  });
  await expect(page.locator("#jobs")).toContainText("Download COMPLETE");
  await expect(page.locator("#jobs")).toContainText("Coverage UNKNOWN");
  await expect(page.locator("#jobs")).not.toContainText("99%");
  await expect(page.locator("#jobs")).not.toContainText("100%");
  await expect(page.locator("#jobs progress").first()).not.toHaveAttribute(
    "value",
  );
  await expect(page.locator("#overall")).toContainText(
    "1/1 downloads complete",
  );
  await expect(page.locator("#connection-notice")).toHaveAttribute(
    "role",
    "alert",
  );
});

test("1205 instruments use global server sorting, header cycle, filters and reload persistence", async ({
  page,
}) => {
  const requests = [];
  const universe = Array.from({ length: 1205 }, (_, i) => ({
    category: "linear",
    symbol: `COIN${String(i).padStart(4, "0")}`,
    status: i % 2 ? "Trading" : "Closed",
    contract_kind: i % 3 ? "perpetual" : "dated",
    metrics: { price: m(String(i)) },
  }));
  await setup(page, {
    pagePayload(u) {
      if (!u.pathname.startsWith("/api/market-catalog/")) return;
      requests.push(u);
      let rows = universe.filter(
        (r) =>
          r.symbol.includes(u.searchParams.get("search") || "") &&
          (!u.searchParams.get("status") ||
            r.status === u.searchParams.get("status")) &&
          (!u.searchParams.get("contract_kind") ||
            r.contract_kind === u.searchParams.get("contract_kind")),
      );
      const saved = u.searchParams.getAll("watchlist");
      if (saved.length)
        rows = rows.filter((r) => saved.includes(`${r.category}:${r.symbol}`));
      if (
        u.searchParams.get("sort") === "price" &&
        u.searchParams.get("direction") !== "none"
      )
        rows.sort(
          (a, b) =>
            (Number(a.metrics.price.value) - Number(b.metrics.price.value)) *
            (u.searchParams.get("direction") === "desc" ? -1 : 1),
        );
      const offset = Number(u.searchParams.get("offset"));
      return {
        schema_version: "1.0",
        data: {
          rows: rows.slice(offset, offset + 50),
          total: rows.length,
          revision: "large-1",
          catalog: {
            loaded: 1205,
            completeness: "EXHAUSTED",
            state: "CURRENT",
            last_success: 1704067200,
            next_refresh_at: Date.now() / 1000 + 300,
          },
        },
      };
    },
  });
  await expect(page.locator("[data-row]")).toHaveCount(50);
  await page
    .getByRole("combobox", { name: "Status", exact: true })
    .selectOption("");
  await page.getByRole("button", { name: "Sort Price", exact: true }).click();
  await expect(page.locator('th[aria-sort="ascending"]')).toContainText(
    "Price",
  );
  await page
    .getByRole("button", { name: "Sort Price", exact: true })
    .press("Enter");
  await expect(page.locator("[data-row]").first()).toHaveAttribute(
    "data-row",
    "linear:COIN1204",
  );
  await page.getByLabel("Select COIN1204").check();
  await page.getByRole("button", { name: "Save watchlist" }).click();
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  await expect(page.locator("[data-row]").first()).toHaveAttribute(
    "data-row",
    "linear:COIN1154",
  );
  await page.getByLabel("Saved only").check();
  await expect(page.locator("[data-row]")).toHaveCount(1);
  await page.reload();
  await expect(page.locator("[data-row]")).toHaveCount(1);
  await expect(page.getByLabel("Select COIN1204")).toBeChecked();
  expect(
    requests.some(
      (u) =>
        u.searchParams.get("offset") === "50" &&
        u.searchParams.get("direction") === "desc",
    ),
  ).toBeTruthy();
  expect(requests.at(-1).searchParams.getAll("watchlist")).toEqual([
    "linear:COIN1204",
  ]);
  await page.getByRole("button", { name: "Sort Price", exact: true }).click();
  await expect(
    page.locator('th[aria-sort="ascending"],th[aria-sort="descending"]'),
  ).toHaveCount(0);
});

test("inline jobs retain category identity and independent IDs with unknown coverage", async ({
  page,
}) => {
  let post = 0;
  await setup(page, {
    pagePayload(u) {
      if (u.pathname === "/api/downloads")
        return {
          schema_version: "1.0",
          data: { id: `inline${++post}`, state: "completed" },
        };
    },
  });
  await page
    .getByRole("button", { name: "Chart BTCUSDT", exact: true })
    .click();
  await page.getByLabel("Select ETHUSDT").check();
  expect(post).toBe(0);
  await page
    .getByRole("button", { name: "Download BTCUSDT", exact: true })
    .click();
  await expect(
    page.locator('[data-row="linear:BTCUSDT"] [data-job-id]'),
  ).toHaveCount(1);
  await page
    .getByRole("button", { name: "Download BTCUSDT", exact: true })
    .click();
  await expect(
    page.locator('[data-row="linear:BTCUSDT"] [data-job-id]'),
  ).toHaveCount(2);
  await expect(page.locator('[data-row="linear:BTCUSDT"]')).toContainText(
    "Download COMPLETE",
  );
  await expect(page.locator('[data-row="linear:BTCUSDT"]')).toContainText(
    "Coverage UNKNOWN",
  );
  await expect(
    page.locator('[data-row="linear:BTCUSDT"] progress').first(),
  ).not.toHaveAttribute("value");
  await expect(
    page.locator('[data-row="linear:ETHUSDT"] [data-job-id]'),
  ).toHaveCount(0);
  expect(await page.locator("#interval option").count()).toBe(13);
});

test("every catalog metric and text header requests global server sort", async ({
  page,
}) => {
  await setup(page);
  for (const header of await page.locator("#catalog-head button").all()) {
    const wait = page.waitForRequest(
      (r) =>
        r.url().includes("/api/market-catalog/") &&
        r.url().includes("direction=asc"),
    );
    await header.click();
    await wait;
  }
  await page
    .getByRole("combobox", { name: "Market", exact: true })
    .selectOption("inverse");
  await expect
    .poll(() =>
      page.evaluate(
        () => JSON.parse(localStorage.getItem("bybit-catalog-view")).category,
      ),
    )
    .toBe("inverse");
});
