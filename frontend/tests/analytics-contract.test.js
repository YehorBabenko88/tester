import test from "node:test";
import assert from "node:assert/strict";
import {
  ANALYTICS_METHOD,
  QUANTITY_UNITS,
  analyticsContractLabel,
  classifyAnalyticsReadback,
  classifyStoredAnalyticsMetrics,
} from "../analytics-contract.js";

const capabilities = () => ({
  source_contracts: {
    public_executions: {
      quantity_unit: {
        linear: "source_native_linear_size",
        inverse: "source_native_inverse_contract",
      },
    },
    observed_execution_metrics: {
      contract_version: ANALYTICS_METHOD,
      write_version: ANALYTICS_METHOD,
      pure_calculator: "src.analytics.research_metrics.calculate_v2",
      readback_state: "NOT_BOUND_TO_UI_ROUTE",
      quantity_unit: { spot: "base", linear: "base", inverse: "quote" },
    },
  },
});
test("advertised math source cannot authorize a disconnected UI readback", () => {
  for (const category of ["spot", "linear", "inverse"]) {
    for (const panel of ["footprint", "delta", "cvd", "vwap", "poc"]) {
      const result = classifyAnalyticsReadback(capabilities(), category, panel);
      assert.equal(result.requestAllowed, false);
      assert.equal(result.strategyAdmissionAllowed, false);
      assert.equal(result.coverage, "UNKNOWN");
    }
  }
  assert.match(
    analyticsContractLabel(classifyAnalyticsReadback(capabilities(), "inverse", "vwap")),
    /UNVERIFIED/,
  );
});
test("forged route registration and malformed contracts cannot grant admission", () => {
  for (const mutate of [
    (value) => value.source_contracts.observed_execution_metrics.readback_state =
      "BOUND_TO_VERIFIED_UI_ROUTE",
    (value) => value.source_contracts.observed_execution_metrics.contract_version =
      "tester.execution-metrics/1",
    (value) => value.source_contracts.observed_execution_metrics.quantity_unit.inverse =
      "base",
    (value) => value.source_contracts.public_executions.quantity_unit.inverse =
      "source_native_linear_size",
    (value) => delete value.source_contracts.observed_execution_metrics,
  ]) {
    const value = capabilities();
    mutate(value);
    assert.equal(classifyAnalyticsReadback(value, "inverse", "vwap").requestAllowed, false);
  }
  assert.equal(classifyAnalyticsReadback({}, "linear", "delta").requestAllowed, false);
  assert.equal(classifyAnalyticsReadback(capabilities(), "linear", "unknown").requestAllowed, false);
});
test("v2 metrics retain exact native unit and never authorize F", () => {
  for (const [category, quantity_unit] of Object.entries(QUANTITY_UNITS)) {
    const result = classifyStoredAnalyticsMetrics({
      method: ANALYTICS_METHOD,
      market: { category },
      quantity_unit,
      admitted_to_f: false,
    }, category);
    assert.equal(result.status, "CURRENT");
    assert.equal(result.quantityUnit, quantity_unit);
    assert.equal(result.strategyAdmissionAllowed, false);
    assert.match(analyticsContractLabel(result), /strategy admission DENIED/);
  }
});
test("historical v1 only linear or spot, never relabelled to v2", () => {
  for (const category of ["spot", "linear"]) {
    const metrics = { method: "tester.execution-metrics/1", market: { category },
      admitted_to_f: false };
    const result = classifyStoredAnalyticsMetrics(metrics, category);
    assert.equal(result.status, "HISTORICAL_READ_ONLY");
    assert.equal(result.method, "tester.execution-metrics/1");
    assert.equal(result.quantityUnit, "base");
    assert.equal(result.strategyAdmissionAllowed, false);
    assert.equal(classifyStoredAnalyticsMetrics({ ...metrics, quantity_unit: "base" },
      category).status, "UNVERIFIED");
  }
  assert.equal(classifyStoredAnalyticsMetrics({
    method: "tester.execution-metrics/1",
    market: { category: "inverse" },
    admitted_to_f: false,
  }, "inverse").status, "UNVERIFIED");
});
test("mismatch, null and claimed strategy admission are rejected", () => {
  const good = { method: ANALYTICS_METHOD, market: { category: "inverse" },
    quantity_unit: "quote", admitted_to_f: false };
  for (const value of [
    null, [], { ...good, admitted_to_f: true }, { ...good, admitted_to_f: "false" },
    { ...good, quantity_unit: "base" }, { ...good, market: { category: "linear" } },
    { ...good, method: "tester.execution-metrics/3" },
  ]) {
    const result = classifyStoredAnalyticsMetrics(value, "inverse");
    assert.equal(result.status, "UNVERIFIED");
    assert.equal(result.strategyAdmissionAllowed, false);
  }
});
