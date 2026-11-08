import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
test.use({ deviceScaleFactor: 2 });
test("synthetic renderer fixture draws persisted markers and clears stale canvas state", async ({
  page,
}) => {
  await page.route("http://terminal.test/**", async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === "/")
      return route.fulfill({
        contentType: "text/html",
        body: '<canvas id="chart" style="width:700px;height:350px"></canvas>',
      });
    return route.fulfill({
      contentType: "text/javascript",
      body: await readFile(path.join(process.cwd(), u.pathname.slice(1))),
    });
  });
  await page.goto("/");
  await page.evaluate(async () => {
    const { ResearchChart } = await import("/research-chart.js");
    const c = new ResearchChart(document.querySelector("canvas"));
    c.set({
      schema_version: "tester.research-chart/2",
      execution_mode: "SIMULATED",
      synthetic: true,
      coverage: "complete",
      source_ids: ["synthetic:fixture"],
      venue: "bybit",
      run_id: "r1",
      snapshot_id: "synthetic:s1",
      revision: "rev1",
      symbol: "BTCUSDT",
      category: "linear",
      interval: "1",
      start_ms: 1000,
      end_ms: 3000,
      cutoff_ms: 3000,
      candles: [
        {
          start_ms: 1000,
          end_ms: 2000,
          open: "100",
          high: "110",
          low: "90",
          close: "105",
        },
        {
          start_ms: 2000,
          end_ms: 3000,
          open: "105",
          high: "112",
          low: "95",
          close: "100",
        },
      ],
      markers: [
        {
          fill_id: "f1",
          order_id: "o1",
          position_id: "p1",
          closed_trade_id: null,
          timestamp_ms: 1999,
          known_at_ms: 2000,
          window_start_ms: 1000,
          window_end_ms: 2000,
          time_precision: "bar",
          role: "entry",
          side: "buy",
          price: "100.000000000000001",
        },
        {
          fill_id: "f2",
          order_id: "o2",
          position_id: "p1",
          closed_trade_id: "t1",
          timestamp_ms: 2499,
          known_at_ms: 3000,
          window_start_ms: 1000,
          window_end_ms: 3000,
          time_precision: "bar",
          role: "exit",
          side: "sell",
          price: "105",
        },
      ],
    });
    document
      .querySelector("canvas")
      .addEventListener("test-clear", () => c.clear());
  });
  const canvas = page.locator("canvas");
  await expect(canvas).toHaveAttribute("data-rendered-fill-ids", '["f1","f2"]');
  const pixels = await canvas.evaluate((c) => {
    const ctx = c.getContext("2d"),
      entry = JSON.parse(c.dataset.renderedGeometry).markers[0],
      dpr = c.width / c.clientWidth;
    return {
      entry: [
        ...ctx.getImageData(
          Math.floor(entry.x * dpr),
          Math.floor(entry.y * dpr),
          1,
          1,
        ).data,
      ],
      // Second candle's high wick, above its body and both fill markers.
      wick: [...ctx.getImageData(330 * dpr, 38 * dpr, 1, 1).data],
    };
  });
  expect(pixels.entry).toEqual([243, 194, 115, 255]);
  expect(pixels.wick).toEqual([246, 132, 138, 255]);
  expect(
    await canvas.evaluate(
      (c) => c.getContext("2d").getImageData(350, 100, 1, 1).data[3],
    ),
  ).toBeGreaterThan(0);
  await canvas.focus();
  await page.keyboard.press("+");
  await page.keyboard.press("ArrowRight");
  const g = JSON.parse(await canvas.getAttribute("data-rendered-geometry"));
  for (const m of g.markers) {
    expect(m.x).toBeGreaterThanOrEqual(g.bounds.left);
    expect(m.x).toBeLessThanOrEqual(g.bounds.right);
  }
  await canvas.dispatchEvent("test-clear");
  await expect(canvas).toHaveAttribute("data-rendered-fill-ids", "[]");
  await expect(canvas).not.toHaveAttribute("data-rendered-geometry");
});
