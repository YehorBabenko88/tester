import test from "node:test";
import assert from "node:assert/strict";
import { inspectorScope, inspectCapabilityPayload, mountAnalyticsInspector } from "../analytics-inspector.js";
import { request } from "../api.js";

const scope = () => ({
  category: "inverse", symbol: "BTCUSD",
  interval: "1", start: "2026-11-25T00:00",
  end: "2026-11-25T01:00",
});
function dom(metric = "vwap") {
  const nodes = new Map();
  for (const id of [
    "#analytics-inspector-form", "#analytics-inspector-status",
    "#analytics-inspector-evidence", "#analytics-inspector-submit",
    "#analytics-inspector-metric",
  ]) nodes.set(id, {
    value: id.endsWith("metric") ? metric : "",
    textContent: "", disabled: false, listeners: {},
    addEventListener(name, callback) { this.listeners[name] = callback; },
  });
  return { root: { querySelector: (s) => nodes.get(s) },
    node: (s) => nodes.get("#analytics-inspector-" + s) };
}
const payload = () => ({
  schema_version: "1.0",
  data: {
    protocol_version: "tester.ui-capabilities/1",
    execution_source_evidence: {
      status: "UNVERIFIED", historical_coverage: "UNKNOWN",
    },
    source_contracts: {
      public_executions: {
        quantity_unit: {
          inverse: "source_native_inverse_contract",
          linear: "source_native_linear_size",
        },
      },
      observed_execution_metrics: {
        contract_version: "tester.execution-metrics/2",
        write_version: "tester.execution-metrics/2",
        pure_calculator: "src.analytics.research_metrics.calculate_v2",
        readback_state: "NOT_BOUND_TO_UI_ROUTE",
        quantity_unit: { inverse: "quote", linear: "base", spot: "base" },
      },
    },
  },
});
test("inspector scope checks UTC, instrument, category, interval and finite 7-day bound", () => {
  const checked = inspectorScope({ ...scope(), metric: "vwap" });
  assert.equal(checked.category, "inverse");
  assert.equal(checked.symbol, "BTCUSD");
  assert.equal(checked.end_ms - checked.start_ms, 3600000);
  for (const bad of [
    { category: "unknown" }, { symbol: "../bad" },
    { interval: "bad" }, { metric: "unlisted" },
    { start: "2026-11-25T02:00" }, { end: "2026-12-10T00:00" },
    { start: "2026-02-30T00:00" },
  ]) assert.throws(() => inspectorScope({ ...scope(), metric: "vwap", ...bad }));
});
test("every canonical quality metric remains unverified until real source-backed authority exists", () => {
  for (const metric of ["hourly_range_percent", "rolling_60_minute_range_percent",
    "realized_volatility", "reconcile_minute_volume", "compare_candles",
    "vwap", "footprint", "delta", "cvd", "poc"]) {
    const result = inspectCapabilityPayload(payload().data, inspectorScope({
      ...scope(), metric,
    }));
    assert.equal(result.strategyAdmissionAllowed, false);
    assert.equal(result.requestAllowed, false);
    assert.equal(result.historicalCoverage, "UNKNOWN");
    assert.equal(result.state, "UNVERIFIED");
  }
});
test("real capabilities URL is queried once; absence of analytics route never causes speculative second request", async () => {
  const { root, node } = dom();
  const paths = [];
  const fetcher = async (path, options) => {
    paths.push({ path, options });
    return new Response(JSON.stringify(payload()), { status: 200 });
  };
  const inspector = mountAnalyticsInspector(root, {
    getScope: scope,
    fetchCapabilities: (params, signal) =>
      request("/api/capabilities", params, { signal, fetcher }),
  });
  await inspector.inspect();
  assert.equal(paths.length, 1);
  assert.match(paths[0].path, /^\/api\/capabilities\?/);
  assert.match(paths[0].path, /category=inverse/);
  assert.match(paths[0].path, /symbol=BTCUSD/);
  assert.match(node("status").textContent, /UNVERIFIED/);
  assert.match(node("status").textContent, /unit quote/);
  assert.match(node("status").textContent, /strategy admission DENIED/);
  assert.match(node("evidence").textContent, /NO analytics data request sent/);
  assert.equal(node("submit").disabled, false);
});
test("stale selection or cancelled request never replaces newer evidence", async () => {
  const { root, node } = dom();
  let selection = scope(), complete;
  const inspector = mountAnalyticsInspector(root, {
    getScope: () => selection,
    fetchCapabilities: () => new Promise((resolve) => { complete = resolve; }),
  });
  const pending = inspector.inspect();
  selection = { ...selection, symbol: "ETHUSD" };
  complete(payload());
  await pending;
  assert.match(node("status").textContent, /Selection changed/);
  const next = inspector.inspect();
  inspector.reset();
  complete(payload());
  await next;
  assert.match(node("status").textContent, /No verified analytics readback/);
  assert.equal(node("submit").disabled, false);
});
test("failed or mismatched capability versions cannot display verified data", async () => {
  const { root, node } = dom("hourly_range_percent");
  const inspector = mountAnalyticsInspector(root, {
    getScope: scope,
    fetchCapabilities: async () => {
      throw Error("provider disconnected");
    },
  });
  await inspector.inspect();
  assert.match(node("status").textContent, /DATA_UNAVAILABLE/);
  assert.equal(node("submit").disabled, false);
  for (const value of [null, {}, { ...payload().data, protocol_version: "v2" }]) {
    const result = inspectCapabilityPayload(value, inspectorScope({
      ...scope(), metric: "vwap",
    }));
    assert.equal(result.requestAllowed, false);
    assert.equal(result.strategyAdmissionAllowed, false);
    assert.equal(result.historicalCoverage, "UNKNOWN");
  }
});
