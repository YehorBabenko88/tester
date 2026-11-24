import { test } from "node:test";
import assert from "node:assert/strict";
import {
  compareDecimal,
  metric,
  progress,
  validateSeries,
  connectionNotice,
} from "../model.js";
import { request } from "../api.js";
import { plotGeometry, barDomain, isSeriesGap } from "../charts.js";
test("OI line continuity follows source cadence rather than display timeframe", () => {
  assert.equal(isSeriesGap(0, { ts: 2 * 3600000 }, "D", "5min"), true);
  assert.equal(isSeriesGap(0, { ts: 86400000 }, "60", "1d"), false);
  assert.equal(isSeriesGap(0, { ts: 5 * 60000 }, "D", "5min"), false);
});
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
test("download work completion is distinct from coverage and legacy progress", () => {
  assert.equal(
    progress({
      state: "COMPLETE",
      progress: { completed: 10, total: 10, verified: true },
    }).percent,
    null,
  );
  assert.equal(
    progress({ state: "completed", progress: 1 }).coverage,
    "UNKNOWN",
  );
  const telemetry = {
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
  };
  const complete = progress({ state: "completed", checkpoint: { telemetry } });
  assert.equal(complete.percent, null);
  assert.equal(complete.coverage, "UNKNOWN");
  assert.equal(complete.records_verified, 10);
  for (const coverage of ["UNKNOWN", "PARTIAL", "VERIFIED"]) {
    assert.equal(
      progress({
        state: "completed",
        checkpoint: { telemetry: { ...telemetry, coverage } },
      }).coverage,
      coverage,
    );
  }
  assert.equal(
    progress({
      checkpoint: { telemetry: { ...telemetry, work_complete: "true" } },
    }).percent,
    null,
  );

  assert.equal(
    progress({
      state: "running",
      checkpoint: { telemetry: { ...telemetry, work_complete: false } },
    }).percent,
    null,
  );
  assert.equal(
    progress({
      state: "running",
      checkpoint: {
        telemetry: { ...telemetry, work_complete: false, expected_records: 20 },
      },
    }).percent,
    50,
  );
  assert.equal(
    progress({
      state: "running",
      checkpoint: {
        telemetry: { ...telemetry, work_complete: false, expected_records: 10 },
      },
    }).percent,
    null,
  );
  assert.equal(
    progress({
      checkpoint: { telemetry: { ...telemetry, schema_version: 99 } },
    }).records_verified,
    null,
  );
  assert.equal(
    progress({
      checkpoint: { telemetry: { ...telemetry, coverage: "COMPLETE" } },
    }).coverage,
    "UNKNOWN",
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

test("HTTP notices distinguish loopback from remote and do not claim TLS", () => {
  for (const host of ["localhost", "127.0.0.1", "[::1]"]) {
    assert.equal(
      connectionNotice({ protocol: "http:", hostname: host }).warning,
      false,
    );
  }
  assert.equal(
    connectionNotice({ protocol: "http:", hostname: "localhost.example" })
      .warning,
    true,
  );
  assert.equal(
    connectionNotice({ protocol: "https:", hostname: "example.com" }),
    null,
  );
});
