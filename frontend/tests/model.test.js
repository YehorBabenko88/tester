import { test } from "node:test";
import assert from "node:assert/strict";
import { compareDecimal, metric, progress, validateSeries } from "../model.js";
import { request } from "../api.js";
import { plotGeometry, barDomain } from "../charts.js";
test("chart forward and inverse coordinates share the exact plot area", () => {
  const g = plotGeometry(800, [1000, 2000]);
  assert.equal(g.timeAt(g.x(1750)), 1750);
  assert.equal(g.timeAt(0), 1000);
  assert.equal(g.timeAt(800), 2000);
  assert.deepEqual(barDomain([100, 101], "volume"), [0, 101]);
  assert.deepEqual(barDomain([-101, -100], "delta"), [-101, 0]);
});
test("sort exact decimals without rounding and leave unavailable last", () => {
  assert.equal(
    compareDecimal("9007199254740992.001", "9007199254740992.002"),
    -1,
  );
  assert.equal(compareDecimal(null, "1", -1), 1);
  assert.equal(compareDecimal("-0.02", "-0.1"), 1);
});
test("metric cannot claim available without source and timestamp", () => {
  assert.equal(
    metric({ value: "1", availability: "AVAILABLE" }).availability,
    "DATA_UNAVAILABLE",
  );
  assert.equal(
    metric({
      value: "1",
      availability: "AVAILABLE",
      source: "Bybit",
      as_of: 1000,
    }).value,
    "1",
  );
});
test("progress never claims 100 before verified completion", () => {
  assert.equal(
    progress({ state: "DOWNLOADING", progress: { completed: 10, total: 10 } })
      .percent,
    99,
  );
  assert.equal(
    progress({
      state: "COMPLETE",
      progress: { completed: 10, total: 10, verified: true },
    }).percent,
    100,
  );
  assert.equal(
    progress({ state: "DISCOVERING", progress: { completed: 10 } }).percent,
    null,
  );
  assert.equal(
    progress({ state: "COMPLETE", progress: { completed: 10, total: 10 } })
      .percent,
    99,
  );
});
test("corrupt candles are rejected, gaps preserved and bounded", () => {
  assert.throws(
    () =>
      validateSeries(
        [{ ts: 1, open: "4", high: "2", low: "1", close: "3" }],
        "candles",
        0,
        10,
      ),
    /corrupt/i,
  );
  assert.throws(
    () => validateSeries([{ ts: 10, value: "1" }], "delta", 0, 10),
    /range/i,
  );
  assert.throws(
    () => validateSeries([{ ts: 2, value: "NaN" }], "delta", 0, 10),
    /decimal/i,
  );
  assert.equal(
    validateSeries([{ ts: 1, value: "0" }], "delta", 0, 10).length,
    1,
  );
});
test("HTTP and schema failures remain explicit errors", async () => {
  await assert.rejects(
    request(
      "/api/instruments",
      {},
      {
        fetcher: async () =>
          new Response(
            JSON.stringify({
              error: { code: "provider_unavailable", message: "offline" },
            }),
            { status: 503 },
          ),
      },
    ),
    /provider_unavailable/,
  );
  await assert.rejects(
    request(
      "/api/instruments",
      {},
      { fetcher: async () => new Response("{}") },
    ),
    /schema/i,
  );
});
