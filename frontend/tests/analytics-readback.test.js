import test from "node:test";
import assert from "node:assert/strict";
import {
  observedScope, validatedObservedReadback, mountObservedReadback,
} from "../analytics-readback.js";

const fields = () => ({
  category: "inverse", symbol: "BTCUSD", interval: "60",
  start: "2026-11-25T00:00", end: "2026-11-25T01:00",
  as_of: "2026-11-26T00:00", tick_size: "0.5",
});
const capability = () => ({ data: {
  features: { analytics_readback: {
    enabled: true,
    resource: "/api/analytics/readback",
    status: "OBSERVED_ONLY_NO_F_ADMISSION",
  }},
}});
const sample = (scope) => ({
  schema_version: "1.0",
  data: {
    schema_version: "tester.analytics-readback/1",
    status: "OBSERVED_UNVERIFIED",
    reason: "historical_completeness_not_proven",
    admitted_to_f: false,
    market: { venue: "bybit", category: scope.category, symbol: scope.symbol },
    method: "tester.execution-metrics/2",
    quantity_unit: scope.category === "inverse" ? "quote" : "base",
    source_quantity_unit: scope.category === "inverse"
      ? "source_native_inverse_contract" : "source_native_linear_size",
    window: { start_ms: scope.start_ms, end_ms: scope.end_ms,
      interval_ms: scope.interval_ms, as_of_ms: scope.as_of_ms },
    source: { publication_time: "UNKNOWN" },
    quality: { status: "UNVERIFIED", coverage: "UNKNOWN", admitted_to_f: false },
    metrics: {
      method: "tester.execution-metrics/2",
      status: "DERIVED_UNVERIFIED", admitted_to_f: false,
      quantity_unit: scope.category === "inverse" ? "quote" : "base",
      bars: [{ start_ms: scope.start_ms, end_ms: scope.end_ms,
        volume: "100", vwap: "100.50" }],
    },
  },
});
function fixture() {
  const nodes = new Map();
  for (const key of ["form", "status", "data", "as-of", "tick", "submit"]) {
    const node = {
      textContent: "", value: "", disabled: false, handlers: {},
      addEventListener(name, callback) { this.handlers[name] = callback; },
    };
    nodes.set("#analytics-readback-" + key, node);
  }
  nodes.get("#analytics-readback-as-of").value = fields().as_of;
  nodes.get("#analytics-readback-tick").value = fields().tick_size;
  return {
    root: { querySelector: (selector) => nodes.get(selector) },
    node: (key) => nodes.get("#analytics-readback-" + key),
  };
}
test("source-backed scope is explicit, finite and rejects period/identity confusion", () => {
  const s = observedScope(fields());
  assert.equal(s.interval_ms, 3600000);
  assert.equal(s.limit, 99);
  assert.equal(s.end_ms - s.start_ms, 3600000);
  for (const mutation of [
    { category: "spot" }, { symbol: "BTC-USD" }, { symbol: "A" },
    { tick_size: "" }, { tick_size: "NaN" }, { tick_size: "1e-2" },
    { tick_size: "0" }, { tick_size: "-1" }, { tick_size: "0.000" },
    { interval: "M" }, { interval: "W" },
    { end: "2026-11-25T00:00" }, { end: "2026-12-20T00:00" },
    { as_of: "2026-11-24T00:00" },
    { end: "2026-11-25T01:01" },
    { start: "2026-02-30T00:00" },
  ]) assert.throws(() => observedScope({ ...fields(), ...mutation }));
});
test("exact source observation remains unknown coverage and no F grant", () => {
  const scope = observedScope(fields());
  const data = validatedObservedReadback(sample(scope), scope);
  assert.equal(data.quality.coverage, "UNKNOWN");
  assert.equal(data.metrics.admitted_to_f, false);
  assert.equal(data.quantity_unit, "quote");
  for (const mutation of [
    d => d.admitted_to_f = true,
    d => d.quality.coverage = "COMPLETE",
    d => d.quality.status = "VERIFIED",
    d => d.metrics.admitted_to_f = true,
    d => d.metrics.quantity_unit = "base",
    d => d.market.symbol = "ETHUSD",
    d => d.source.publication_time = "KNOWN",
    d => d.method = "tester.execution-metrics/1",
    d => d.metrics.bars[0].vwap = "NaN",
    d => d.metrics.bars[0].end_ms += 60_000,
  ]) {
    const payload = sample(scope);
    mutation(payload.data);
    assert.throws(() => validatedObservedReadback(payload, scope));
  }
  assert.throws(() => validatedObservedReadback(null, scope));
});
test("disabled advertised route never sends analytics readback", async () => {
  const { root, node } = fixture();
  let backendReads = 0;
  const viewer = mountObservedReadback(root, {
    getScope: fields,
    fetchCapabilities: async () => ({ data: { features: {} } }),
    fetchReadback: async () => { backendReads++; throw Error("not allowed"); },
  });
  await viewer.inspect();
  assert.equal(backendReads, 0);
  assert.match(node("status").textContent, /verified_analytics_route_not_registered/);
  assert.equal(node("submit").disabled, false);
});
test("connected observed-only route displays bounded exact values without claiming completeness", async () => {
  const { root, node } = fixture();
  const calls = [];
  const viewer = mountObservedReadback(root, {
    getScope: fields,
    fetchCapabilities: async (scope) => {
      calls.push(["capabilities", scope]);
      return capability();
    },
    fetchReadback: async (scope) => {
      calls.push(["readback", scope]);
      return sample(scope);
    },
  });
  await viewer.inspect();
  assert.deepEqual(calls.map(x => x[0]), ["capabilities", "readback"]);
  assert.match(node("status").textContent, /OBSERVED_UNVERIFIED/);
  assert.match(node("status").textContent, /historical coverage UNKNOWN/);
  assert.match(node("status").textContent, /strategy admission DENIED/);
  assert.match(node("data").textContent, /100.50/);
  assert.match(node("data").textContent, /observed sample only/);
});
test("async stale selection and resetting inputs cannot display an old source sample", async () => {
  const { root, node } = fixture();
  let resolve, entered, current = fields();
  const readStarted = new Promise(done => { entered = done; });
  const viewer = mountObservedReadback(root, {
    getScope: () => current,
    fetchCapabilities: async () => capability(),
    fetchReadback: () => {
      entered();
      return new Promise(done => { resolve = done; });
    },
  });
  const pending = viewer.inspect();
  await readStarted;
  current = { ...current, symbol: "ETHUSD" };
  resolve(sample(observedScope(fields())));
  await pending;
  assert.doesNotMatch(node("status").textContent, /OBSERVED_UNVERIFIED/);
  assert.equal(node("submit").disabled, false);
});
