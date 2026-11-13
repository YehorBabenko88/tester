import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { adaptSeries } from "../series.js";
import { request } from "../api.js";
import { classifyPanelCapability } from "../capabilities.js";

const app = readFileSync(new URL("../app.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const intervals = ["1","3","5","15","30","60","120","240","360","720","D","W","M"];

test("real timeframe selector provides exactly 13 Bybit intervals", () => {
  const options = [...html.matchAll(/<option value="([^"]+)"[^>]*>[^<]+<\/option>/g)]
    .map(m => m[1]);
  for (const interval of intervals)
    assert.equal(options.filter(v => v === interval).length, 1, interval);
});
test("real frontend gates every chart request with verified resource", () => {
  assert.match(app, /classifyPanelCapability\(kind, capabilities\[kind\]\)/);
  assert.match(app, /if \(!capability\.requestAllowed\)/);
  assert.match(app, /const path = capability\.resource/);
  assert.match(app, /schemaVersion: capability\.schema_version/);
  assert.doesNotMatch(app, /panelEndpoint\(kind\)/);
});
test("price-only requests use the advertised kind and schema v1.1", () => {
  assert.match(app, /capability\.candle_kind \? \{ candle_kind: capability\.candle_kind \}/);
  assert.match(app, /schema_version: "1\.1"/);
  assert.match(app, /Mark\/Index response provenance does not match/);
  for (const kind of ["mark", "index"]) assert.equal(
    classifyPanelCapability(kind, null).requestAllowed, false);
});
test("explicit retry invalidates discovery and unknown coverage is not probed", () => {
  assert.match(app, /if \(options\.refreshCapabilities\)/);
  assert.match(app, /loadCharts\(\{ refreshCapabilities: true \}\)/);
  assert.match(app, /Aggregate \/api\/coverage has no independent discovery contract/);
  assert.doesNotMatch(app, /loadQuality\(q, abort\.signal\)/);
});
const source = "https://api.bybit.com/v5/market/mark-price-kline?category=linear&symbol=BTCUSDT&interval=1";
const record = {
  schema_version: "1.1", venue: "bybit", category: "linear",
  symbol: "BTCUSDT", timestamp_ms: 0, start_ms: 0, end_ms: 60000,
  interval: "1", kind: "official_mark", is_closed: true,
  open: "100.00001", high: "101", low: "99", close: "100.5",
  availability: { status: "PARTIAL", reason: "observed" },
  coverage: { status: "UNKNOWN", start_ms: 0, end_ms: 60000, evidence_refs: [] },
  provenance: [{ source_kind: "bybit_rest", source_ref: source }],
};
const price = {
  schema_version: "1.1", data: [record], next_cursor: null,
  availability: { status: "PARTIAL", reason: "observed" },
  coverage: record.coverage, provenance: record.provenance,
};
const q = { category: "linear", symbol: "BTCUSDT", interval: "1", start_ms: 0, end_ms: 60000 };

test("price-only v1.1 renders exact closed Mark values without inventing volume", () => {
  const result = adaptSeries(price, "mark", q);
  assert.equal(result.renderable, true);
  assert.equal(result.rows[0].ts, 0);
  assert.equal(result.rows[0].value, "100.5");
  assert.equal(result.rows[0].volume, undefined);
  assert.equal(result.completeness, "UNKNOWN");
});
test("trade candle and mismatched source scope cannot masquerade as Mark", () => {
  assert.throws(() => adaptSeries(
    { ...price, schema_version: "1.0" }, "mark", q), /v1.1/);
  assert.throws(() => adaptSeries({
    ...price, data: [{ ...record, kind: "official_index" }],
  }, "mark", q), /identity/);
  assert.throws(() => adaptSeries({
    ...price, data: [{ ...record, end_ms: 120000 }],
  }, "mark", q), /UTC interval/);
});
test("JSON request rejects incompatible API versions before rendering", async () => {
  const fetcher = async () => ({
    ok: true, headers: { get: () => null }, body: null,
    text: async () => JSON.stringify(price),
  });
  await assert.rejects(request("/api/price-candles", {}, { fetcher }), /Unsupported API schema/);
  const result = await request("/api/price-candles", {}, { fetcher, schemaVersion: "1.1" });
  assert.equal(result.schema_version, "1.1");
});
