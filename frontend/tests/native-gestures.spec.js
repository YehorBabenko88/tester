// AUTOMATED_HEADLESS_FIXTURES: browser automation, not physical ADMINIT mouse evidence.
import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { candle, envelope } from "./fixtures/canonical.js";
import { bucketStart, nextBoundary } from "../time.js";

const START = Date.UTC(2024, 0, 1);
const END = Date.UTC(2024, 6, 1);
const EXACT = "100.12345678901234567890123456789";
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
const minute = (value) => Math.floor(Math.round(value) / 60000) * 60000;
const isoInput = (value) => new Date(value).toISOString().slice(0, 16);
const inputTime = (value) => Date.parse(value + "Z");

async function mount(page, interval = "60") {
  const requests = [];
  requests.chartPages = [];
  await page.addInitScript(() => {
    window.__nativeGestureEvents = [];
    for (const type of ["wheel", "pointerdown", "pointerup"]) {
      document.addEventListener(
        type,
        (event) => {
          if (event.target.tagName === "CANVAS")
            window.__nativeGestureEvents.push({
              type,
              x: event.offsetX,
              shift: event.shiftKey,
            });
        },
        true,
      );
    }
    window.__nativeCrosshairs = [];
    const original = CanvasRenderingContext2D.prototype.moveTo;
    CanvasRenderingContext2D.prototype.moveTo = function (x, y) {
      if (this.strokeStyle === "#93a6bf")
        window.__nativeCrosshairs.push({
          panel: this.canvas.closest("[data-panel]")?.dataset.panel,
          x,
          y,
        });
      return original.call(this, x, y);
    };
  });
  await page.route("http://terminal.test/**", async (route) => {
    const url = new URL(route.request().url());
    expect(route.request().method()).toBe("GET");
    if (!url.pathname.startsWith("/api/")) {
      const file = path.join(
        process.cwd(),
        url.pathname === "/" ? "index.html" : url.pathname.slice(1),
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
    if (
      [
        "/api/candles",
        "/api/footprint",
        "/api/open-interest",
        "/api/funding",
        "/api/analytics",
      ].includes(url.pathname)
    ) {
      requests.chartPages.push({
        path: url.pathname,
        query: Object.fromEntries(url.searchParams),
      });
    }
    let payload = { schema_version: "1.0", data: [], next_cursor: null };
    if (url.pathname === "/api/instruments")
      payload.data = [{ category: "linear", symbol: "BTCUSDT", metrics: {} }];
    if (url.pathname === "/api/sources")
      payload.data = [
        { id: "bybit", availability: "partial", kinds: ["candles"] },
      ];
    if (url.pathname === "/api/candles") {
      const query = Object.fromEntries(url.searchParams);
      requests.push(query);
      const start = Number(query.start_ms),
        end = Number(query.end_ms);
      let time = bucketStart(start, query.interval);
      if (time < start) time = nextBoundary(time, query.interval);
      const rows = [];
      while (rows.length < 4 && nextBoundary(time, query.interval) <= end) {
        const row = candle("PARTIAL", "AUTOMATED_HEADLESS_FIXTURE");
        Object.assign(row, {
          timestamp_ms: time,
          start_ms: time,
          end_ms: nextBoundary(time, query.interval),
          interval: query.interval,
          open: EXACT,
          high: "102",
          low: "99",
          close: EXACT,
          volume: "1.000000000000000000000001",
        });
        row.coverage = {
          status: "PARTIAL",
          start_ms: start,
          end_ms: end,
          evidence_refs: ["fixture://gesture-range"],
        };
        rows.push(row);
        time = nextBoundary(time, query.interval);
      }
      payload = envelope(rows, "PARTIAL", "AUTOMATED_HEADLESS_FIXTURE");
      payload.coverage = {
        status: "PARTIAL",
        start_ms: start,
        end_ms: end,
        evidence_refs: ["fixture://gesture-range"],
      };
    }
    await route.fulfill({ json: payload });
  });
  await page.goto("/");
  await page.getByLabel("Range start UTC").fill(isoInput(START));
  await page.getByLabel("Range end UTC").fill(isoInput(END));
  await page.locator("#interval").selectOption(interval);
  await page.getByRole("button", { name: "Chart BTCUSDT" }).click();
  await ready(page);
  return requests;
}
async function ready(page) {
  await expect(page.locator('[data-panel="candles"] canvas')).toBeVisible();
  await expect(page.locator("#chart-status")).toContainText(
    "panels have records",
  );
}
async function bounds(page) {
  return [
    inputTime(await page.locator("#start").inputValue()),
    inputTime(await page.locator("#end").inputValue()),
  ];
}
async function assertRange(page, requests, expected, waveStart = null) {
  await ready(page);
  expect(await bounds(page)).toEqual(expected);
  const query = requests.at(-1);
  expect([Number(query.start_ms), Number(query.end_ms)]).toEqual(expected);
  if (waveStart !== null) {
    const wave = requests.chartPages.slice(waveStart);
    expect(wave.length).toBeGreaterThan(2);
    for (const entry of wave)
      expect([
        Number(entry.query.start_ms),
        Number(entry.query.end_ms),
      ]).toEqual(expected);
  }

  await expect(page.locator("#range-label")).toContainText(
    new Date(expected[0]).toISOString(),
  );
  await expect(page.locator("#range-label")).toContainText(
    new Date(expected[1]).toISOString(),
  );
  await expect(page.locator('[data-panel="volume"] canvas')).toBeVisible();
}
async function canvasPoint(page, ratio) {
  const canvas = page.locator('[data-panel="candles"] canvas');
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  return {
    canvas,
    box,
    x: box.x + 8 + (box.width - 56) * ratio,
    y: box.y + box.height / 2,
  };
}
async function gestureEvent(page, type) {
  return page.evaluate(
    (name) =>
      window.__nativeGestureEvents
        .filter((event) => event.type === name)
        .at(-1),
    type,
  );
}

for (const interval of INTERVALS) {
  test(`headless fixture wheel anchors UTC position on timeframe ${interval}`, async ({
    page,
  }) => {
    const requests = await mount(page, interval);
    let current = [START, END];
    for (const [deltaY, factor] of [
      [-120, 0.7],
      [120, 1.4],
    ]) {
      const point = await canvasPoint(page, 0.37);
      await page.mouse.move(point.x, point.y);
      const waveStart = requests.chartPages.length;
      const request = page.waitForRequest((req) =>
        req.url().includes("/api/candles?"),
      );
      await page.mouse.wheel(0, deltaY);
      await request;
      const event = await gestureEvent(page, "wheel");
      const ratio = Math.max(
        0,
        Math.min(1, (event.x - 8) / (point.box.width - 56)),
      );
      const anchor = current[0] + (current[1] - current[0]) * ratio;
      const span = (current[1] - current[0]) * factor;
      const expected = [
        minute(anchor - span * ratio),
        minute(anchor + span * (1 - ratio)),
      ];
      await assertRange(page, requests, expected, waveStart);
      expect(requests.at(-1).interval).toBe(interval);
      // The minute-resolution UTC inputs may quantize the anchor by under one minute.
      const anchorAfter = expected[0] + (expected[1] - expected[0]) * ratio;
      expect(Math.abs(anchorAfter - anchor)).toBeLessThan(60001);
      current = expected;
    }
  });
}

test("headless fixture left drag pans by the shared plot span without changing duration", async ({
  page,
}) => {
  const requests = await mount(page);
  const point = await canvasPoint(page, 0.55);
  await page.mouse.move(point.x, point.y);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(point.x + 70, point.y, { steps: 5 });
  const request = page.waitForRequest((req) =>
    req.url().includes("/api/candles?"),
  );
  await page.mouse.up({ button: "left" });
  await request;
  const down = await gestureEvent(page, "pointerdown"),
    up = await gestureEvent(page, "pointerup");
  const delta = Math.round(
    ((down.x - up.x) / (point.box.width - 56)) * (END - START),
  );
  const expected = [minute(START + delta), minute(END + delta)];
  await assertRange(page, requests, expected);
  expect(expected[1] - expected[0]).toBe(END - START);
});

test("headless fixture Shift drag selects UTC endpoints across a month boundary", async ({
  page,
}) => {
  const requests = await mount(page, "M");
  const first = await canvasPoint(page, 0.2),
    last = await canvasPoint(page, 0.8);
  await page.keyboard.down("Shift");
  await page.mouse.move(first.x, first.y);
  await page.mouse.down({ button: "left" });
  await page.mouse.move(last.x, last.y, { steps: 5 });
  const request = page.waitForRequest((req) =>
    req.url().includes("/api/candles?"),
  );
  await page.mouse.up({ button: "left" });
  await page.keyboard.up("Shift");
  await request;
  const down = await gestureEvent(page, "pointerdown"),
    up = await gestureEvent(page, "pointerup");
  expect(down.shift).toBe(true);
  const time = (x) =>
    START +
    Math.max(0, Math.min(1, (x - 8) / (first.box.width - 56))) * (END - START);
  const expected = [
    minute(time(Math.min(down.x, up.x))),
    minute(time(Math.max(down.x, up.x))),
  ];
  await assertRange(page, requests, expected);
  expect(expected[0]).toBeGreaterThan(START);
  expect(expected[1]).toBeLessThan(END);
  expect(new Date(expected[0]).getUTCMonth()).not.toBe(
    new Date(expected[1]).getUTCMonth(),
  );
});

test("headless fixture crosshair shares UTC geometry after high-DPI resize and exact source decimals survive", async ({
  browser,
}) => {
  const context = await browser.newContext({
    baseURL: "http://terminal.test",
    deviceScaleFactor: 2,
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  try {
    const requests = await mount(page, "W");
    for (const width of [1280, 900]) {
      await page.setViewportSize({ width, height: 900 });
      const point = await canvasPoint(page, 0.42);
      await expect
        .poll(() =>
          point.canvas.evaluate((canvas) =>
            Math.abs(canvas.width - canvas.clientWidth * devicePixelRatio),
          ),
        )
        .toBeLessThanOrEqual(1);
      await page.evaluate(() => {
        window.__nativeCrosshairs = [];
      });
      await page.mouse.move(point.x, point.y);
      await expect
        .poll(() =>
          page.evaluate(
            () =>
              window.__nativeCrosshairs.filter((value) =>
                ["candles", "volume"].includes(value.panel),
              ).length,
          ),
        )
        .toBeGreaterThanOrEqual(2);
      const crosshair = await page.locator("#crosshair").textContent();
      const timestamp = Date.parse(crosshair);
      const expected = START + (END - START) * 0.42;
      expect(Math.abs(timestamp - expected)).toBeLessThan(
        (END - START) / (point.box.width - 56),
      );
      const lines = await page.evaluate(() => window.__nativeCrosshairs);
      for (const panel of ["candles", "volume"]) {
        const canvas = page.locator(`[data-panel="${panel}"] canvas`);
        const panelWidth = await canvas.evaluate((value) => value.clientWidth);
        const actual = lines.filter((value) => value.panel === panel).at(-1);
        expect(
          Math.abs(
            actual.x -
              (8 + ((timestamp - START) / (END - START)) * (panelWidth - 56)),
          ),
        ).toBeLessThan(0.01);
      }
      expect(await bounds(page)).toEqual([START, END]);
    }
    const panel = page.locator('[data-panel="candles"]');
    await panel
      .getByText("Exact source values (first 100 rows)", { exact: true })
      .click();
    await expect(panel.locator("table")).toContainText(EXACT);
    await expect(panel.locator("table")).toContainText(
      '"volume":"1.000000000000000000000001"',
    );
    expect(requests.at(-1).interval).toBe("W");
  } finally {
    await context.close();
  }
});
