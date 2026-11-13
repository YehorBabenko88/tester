import test from "node:test";
import assert from "node:assert/strict";
import { adaptSeries } from "../series.js";
import { bucketStart, nextBoundary } from "../time.js";

const INTERVALS = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];
const EXACT = "101.250000000000000000000001";
const reference = (kind, interval, ts) => {
  const sourceRef = "https://api.bybit.com/v5/market/" +
    (kind === "mark" ? "mark-price-kline" : "index-price-kline") +
    "?category=linear&symbol=BTCUSDT&interval=" + interval;
  const provenance = [{
    source_kind: "bybit_rest", source_ref: sourceRef,
    source_hash: "a".repeat(64), ingestion_run: "unit-fixture",
    timestamp_precision: "ms", source_timestamp: String(ts),
  }];
  const start_ms = ts, end_ms = nextBoundary(ts, interval);
  const coverage = {
    status: "UNKNOWN", start_ms, end_ms, evidence_refs: [],
  };
  const availability = { status: "PARTIAL", reason: "Bounded observation only" };
  const row = {
    schema_version: "1.1", venue: "bybit", category: "linear",
    symbol: "BTCUSDT", timestamp_ms: ts, start_ms, end_ms, interval,
    kind: kind === "mark" ? "official_mark" : "official_index",
    open: "100.00000000000000000001",
    high: "102.00000000000000000001",
    low: "99.00000000000000000001",
    close: EXACT, is_closed: true, availability, coverage, provenance,
  };
  return {
    q: { category: "linear", symbol: "BTCUSDT", interval, start_ms, end_ms },
    payload: {
      schema_version: "1.1", data: [row], next_cursor: null,
      availability, coverage, provenance,
    },
  };
};

for (const interval of INTERVALS) {
  test("price-only Mark/Index exact Decimal and UTC boundaries " + interval, () => {
    const ts = bucketStart(Date.UTC(2024, 1, 20, 13), interval);
    for (const kind of ["mark", "index"]) {
      const { q, payload } = reference(kind, interval, ts);
      const result = adaptSeries(payload, kind, q);
      assert.equal(result.renderable, true);
      assert.equal(result.rows.length, 1);
      assert.equal(result.rows[0].value, EXACT);
      assert.equal(result.rows[0].ts, ts);
      assert.equal(result.rows[0].volume, undefined);
      assert.equal(result.completeness, "UNKNOWN");
      assert.equal(result.availability, "PARTIAL");
      assert.deepEqual(result.provenance, payload.provenance);
      assert.equal(result.rows[0].end_ms, nextBoundary(ts, interval));
    }
  });
}
test("price-only refuses wrong UTC month/week duration", () => {
  for (const interval of ["W", "M"]) {
    const ts = bucketStart(Date.UTC(2024, 1, 20), interval);
    const { q, payload } = reference("mark", interval, ts);
    payload.data[0].end_ms -= 60000;
    assert.throws(() => adaptSeries(payload, "mark", q), /UTC interval/);
  }
});
test("price-only rejects decimal rounding or incoherent OHLC", () => {
  const ts = Date.UTC(2024, 0, 1);
  for (const change of [{ close: 101.25 }, { high: "100" },
                       { low: "NaN" }, { open: "1e2" }]) {
    const { q, payload } = reference("index", "1", ts);
    Object.assign(payload.data[0], change);
    assert.throws(() => adaptSeries(payload, "index", q), /Decimal or UTC interval/);
  }
});
test("price-only does not promote UNKNOWN history to COMPLETE", () => {
  const { q, payload } = reference("mark", "60", Date.UTC(2024, 0, 1));
  payload.coverage.status = "COMPLETE";
  const out = adaptSeries(payload, "mark", q);
  assert.notEqual(out.completeness, "COMPLETE");
  assert.equal(out.availability, "PARTIAL");
});
