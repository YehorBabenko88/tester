import test from "node:test";
import assert from "node:assert/strict";
import { researchGeometry, validateResearchChart } from "../research-chart.js";
const chart = () => ({
  schema_version: "tester.research-chart/2",
  execution_mode: "SIMULATED",
  synthetic: true,
  coverage: "complete",
  source_ids: ["synthetic:fixture"],
  venue: "bybit",
  run_id: "run1",
  snapshot_id: "synthetic:snapshot1",
  revision: "r1",
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
      open: "100.000000000000001",
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
  ],
});
test("valid research chart retains exact strings, null trade link and uncertainty", () => {
  const c = chart();
  assert.equal(validateResearchChart(c), c);
  assert.equal(c.markers[0].closed_trade_id, null);
  const g = researchGeometry(c, [1000, 3000], 600, 300);
  assert.equal(g.markers.length, 1);
  assert.equal(g.markers[0].source, c.markers[0]);
  assert.ok(g.markers[0].x >= 8 && g.markers[0].x <= 552);
  assert.ok(g.markers[0].window[1] > g.markers[0].window[0]);
});
test("pan, zoom and cutoff retain only in-range known fills and clip windows", () => {
  const c = chart();
  assert.equal(researchGeometry(c, [2000, 3000], 600, 300).markers.length, 0);
  const g = researchGeometry(c, [1500, 2200], 600, 300);
  assert.equal(g.markers[0].window[0], 8);
  c.cutoff_ms = 1500;
  assert.throws(() => validateResearchChart(c), /timing/);
});
test("invalid execution mode, source shape, duplicate fill and bar ambiguity fail closed", () => {
  for (const mutate of [
    (c) => (c.execution_mode = "LIVE"),
    (c) => (c.schema_version = "1"),
    (c) => c.markers.push({ ...c.markers[0] }),
    (c) => (c.markers[0].known_at_ms = 1500),
    (c) => (c.markers[0].timestamp_ms = 2000),
    (c) => (c.candles[0].open = 100),
  ]) {
    const c = chart();
    mutate(c);
    assert.throws(() => validateResearchChart(c));
  }
});
export { chart };
test("canonical digest follows Python sorted compact ASCII JSON", async () => {
  const { researchDigest } = await import("../research-chart.js");
  assert.equal(
    await researchDigest({ z: "\u00e9\ud83d\ude00", a: 1 }),
    "2b0dde880afe45c12ba46f412bcebde34325df5e2ad735a4aed36dcf77e003bd",
  );
});
test("unknown coverage and nonsynthetic snapshot namespace cannot look qualified", () => {
  for (const mutate of [
    (c) => delete c.coverage,
    (c) => (c.coverage = "VERIFIED_REAL"),
    (c) => (c.snapshot_id = "real:snapshot"),
  ]) {
    const c = chart();
    mutate(c);
    assert.throws(() => validateResearchChart(c));
  }
});
