import test from "node:test";
import assert from "node:assert/strict";
import { classifyPanelCapability, panelsToRequest } from "../capabilities.js";

const routes = {
  candles: "/api/candles", volume: "/api/candles",
  footprint: "/api/footprint", open_interest: "/api/open-interest",
  funding: "/api/funding", mark: "/api/price-candles", index: "/api/price-candles",
};
const valid = (panel, status = "PARTIAL") => ({
  panel, status, reason: "bounded_observations_only", resource: routes[panel],
  revision: "page:" + "a".repeat(64), source_ref: "bybit:archive:42",
  schema_version: panel === "mark" || panel === "index" ? "1.1" : "1.0",
  ...(panel === "mark" ? { candle_kind: "official_mark" } : {}),
  ...(panel === "index" ? { candle_kind: "official_index" } : {}),
});

test("absence and unverified panels never trigger queries", () => {
  assert.deepEqual(panelsToRequest({}, ["candles", "footprint", "delta"]), []);
  for (const status of ["UNVERIFIED", "UNAVAILABLE"]) {
    const p = classifyPanelCapability("footprint", { ...valid("footprint"), status });
    assert.equal(p.requestAllowed, false);
    assert.equal(p.state, status);
  }
});
test("valid observed sources retain PARTIAL rather than implying full coverage", () => {
  for (const panel of ["candles", "mark", "index", "open_interest"]) {
    const p = classifyPanelCapability(panel, valid(panel));
    assert.equal(p.requestAllowed, true, panel);
    assert.equal(p.state, "PARTIAL");
  }
});
test("scope, schema and immutable observed digest are required", () => {
  for (const key of ["revision", "source_ref", "resource", "schema_version"]) {
    assert.equal(classifyPanelCapability("candles",
      { ...valid("candles"), [key]: null }).requestAllowed, false, key);
  }
  assert.equal(classifyPanelCapability("mark",
    { ...valid("mark"), schema_version: "1.0" }).requestAllowed, false);
  assert.equal(classifyPanelCapability("index",
    { ...valid("index"), candle_kind: "official_mark" }).requestAllowed, false);
});
test("reject URL injection and cross-panel route substitution", () => {
  assert.equal(classifyPanelCapability("candles",
    { ...valid("candles"), resource: "//evil.example" }).requestAllowed, false);
  assert.equal(classifyPanelCapability("mark",
    { ...valid("mark"), resource: "/api/candles" }).requestAllowed, false);
  assert.equal(classifyPanelCapability("mark",
    { ...valid("mark"), source_ref: "https://evil.example/a" }).requestAllowed, false);
  assert.throws(() => classifyPanelCapability("__proto__", {}), TypeError);
});
test("real Bybit original reference URLs are permitted but not foreign URLs", () => {
  const source = "https://api.bybit.com/v5/market/mark-price-kline?category=linear&symbol=BTCUSDT&interval=1";
  assert.equal(classifyPanelCapability("mark",
    { ...valid("mark"), source_ref: source }).requestAllowed, true);
});
test("unimplemented analytics remain unavailable", () => {
  for (const panel of ["delta", "cvd", "profile", "poc", "vwap"]) {
    assert.equal(classifyPanelCapability(panel,
      { ...valid("candles"), panel }).requestAllowed, false);
  }
  assert.deepEqual(panelsToRequest(
    { mark: valid("mark"), candles: valid("candles") },
    ["mark", "candles", "mark", "delta"]), ["mark", "candles"]);
});
