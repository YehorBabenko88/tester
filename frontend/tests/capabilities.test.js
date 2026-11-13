import test from "node:test";
import assert from "node:assert/strict";
import { classifyPanelCapability, panelsToRequest } from "../../frontend/capabilities.js";

const valid = (panel, status="AVAILABLE") => ({
  panel, status, reason: "source_qualified", resource: "/api/candles",
  revision: "snapshot:42", source_ref: "bybit:archive:42",
});

test("missing endpoint is not requested", () => {
  assert.deepEqual(panelsToRequest({}, ["candles", "footprint", "delta"]), []);
});
test("unknown and unavailable are never upgraded", () => {
  for (const status of ["UNVERIFIED", "UNAVAILABLE"]) {
    const p = classifyPanelCapability("footprint", valid("footprint", status));
    assert.equal(p.requestAllowed, false);
    assert.equal(p.state, status);
  }
});
test("qualified available and partial may be requested, with status retained", () => {
  assert.equal(classifyPanelCapability("candles", valid("candles")).requestAllowed, true);
  assert.equal(classifyPanelCapability("mark", valid("mark", "PARTIAL")).state, "PARTIAL");
});
test("missing provenance or revision rejects optimistic claims", () => {
  assert.equal(classifyPanelCapability("candles", {...valid("candles"), source_ref: ""}).requestAllowed, false);
  assert.equal(classifyPanelCapability("candles", {...valid("candles"), revision: null}).requestAllowed, false);
});
test("no arbitrary URL or panel injection", () => {
  assert.equal(classifyPanelCapability("candles", {...valid("candles"), resource: "//evil.example"}).requestAllowed, false);
  assert.throws(() => classifyPanelCapability("__proto__", {}), TypeError);
});
test("deduplicated request list preserves order", () => {
  const caps = {candles: valid("candles"), mark: valid("mark"), delta: valid("delta", "UNAVAILABLE")};
  assert.deepEqual(panelsToRequest(caps, ["mark", "candles", "mark", "delta"]), ["mark", "candles"]);
});
test("malformed metadata cannot become AVAILABLE", () => {
  for (const record of [[], "AVAILABLE", {status:"AVAILABLE"}, {panel:"mark",status:"AVAILABLE",reason:"<script>"}]) {
    assert.equal(classifyPanelCapability("mark", record).requestAllowed, false);
  }
});

test("no arbitrary analytics or cross-panel endpoint can be advertised", () => {
  for (const panel of ["delta", "cvd", "profile", "poc", "vwap"]) {
    assert.equal(classifyPanelCapability(panel, valid(panel)).requestAllowed, false);
  }
  assert.equal(classifyPanelCapability("mark", {
    ...valid("mark"), resource: "/api/funding"
  }).reason, "unsupported_panel_resource");
  assert.equal(classifyPanelCapability("footprint", {
    ...valid("footprint"), resource: "/api/footprint"
  }).requestAllowed, true);
});
