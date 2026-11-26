// Read-only analytics capability gate. This cannot authorize data requests,
// materialize incomplete history, or grant strategy admission.
// Source contracts are metadata; a registered route needs separately verified
// readback evidence and a dedicated producer/consumer integration.
export const ANALYTICS_METHOD = "tester.execution-metrics/2";
export const QUANTITY_UNITS = Object.freeze({
  spot: "base",
  linear: "base",
  inverse: "quote",
});
const PANELS = new Set(["footprint", "delta", "cvd", "vwap", "poc"]);
const SAFE_STATUS = new Set(["CURRENT", "HISTORICAL_READ_ONLY"]);

export function classifyAnalyticsReadback(payload, category, panel) {
  const deny = (reason) => ({
    status: "UNVERIFIED",
    reason,
    requestAllowed: false,
    strategyAdmissionAllowed: false,
    quantityUnit: null,
    method: null,
    coverage: "UNKNOWN",
  });
  if (!PANELS.has(panel) || !Object.hasOwn(QUANTITY_UNITS, category))
    return deny("unsupported_analytics_scope");
  if (!payload || typeof payload !== "object" || Array.isArray(payload))
    return deny("missing_source_contracts");
  const observed = payload.source_contracts?.observed_execution_metrics;
  const source = payload.source_contracts?.public_executions;
  if (!observed || !source)
    return deny("missing_canonical_source_contract");
  if (observed.contract_version !== ANALYTICS_METHOD ||
      observed.write_version !== ANALYTICS_METHOD ||
      observed.pure_calculator !== "src.analytics.research_metrics.calculate_v2")
    return deny("math_contract_version_mismatch");
  if (observed.quantity_unit?.[category] !== QUANTITY_UNITS[category])
    return deny("quantity_unit_mismatch");
  if (source.quantity_unit?.[category] !== ({
    spot: undefined,
    linear: "source_native_linear_size",
    inverse: "source_native_inverse_contract",
  })[category])
    return deny("source_quantity_unit_mismatch");
  // This source does not provide a public spot execution projection.
  if (category === "spot") return deny("source_backed_spot_not_supported");
  if (observed.readback_state !== "BOUND_TO_VERIFIED_UI_ROUTE")
    return deny("source_readback_not_registered");
  // Capability metadata must never be promoted into a route by an untrusted
  // producer. Route authorization requires an independent integration gate.
  return deny("verified_readback_authority_missing");
}

export function classifyStoredAnalyticsMetrics(metrics, marketCategory) {
  const reject = (reason) => ({
    status: "UNVERIFIED",
    reason,
    quantityUnit: null,
    method: null,
    strategyAdmissionAllowed: false,
  });
  if (!metrics || typeof metrics !== "object" || Array.isArray(metrics))
    return reject("missing_metrics");
  const method = metrics.method;
  const category = metrics.market?.category;
  if (category !== marketCategory || !Object.hasOwn(QUANTITY_UNITS, category))
    return reject("market_category_mismatch");
  if (metrics.admitted_to_f !== false) return reject("unsafe_admission_claim");
  if (method === ANALYTICS_METHOD) {
    if (metrics.quantity_unit !== QUANTITY_UNITS[category])
      return reject("quantity_unit_mismatch");
    return {
      status: "CURRENT",
      reason: "derived_only_not_strategy_admission",
      quantityUnit: metrics.quantity_unit,
      method,
      strategyAdmissionAllowed: false,
    };
  }
  if (method === "tester.execution-metrics/1" && category !== "inverse" &&
      !Object.hasOwn(metrics, "quantity_unit")) {
    return {
      status: "HISTORICAL_READ_ONLY",
      reason: "historical_version_no_upgrade",
      quantityUnit: "base",
      method,
      strategyAdmissionAllowed: false,
    };
  }
  return reject("unknown_or_unsafe_historical_method");
}

export function analyticsContractLabel(result) {
  if (!result || !SAFE_STATUS.has(result.status))
    return "UNVERIFIED · No authorized analytics readback";
  return `${result.status} · ${result.method} · unit ${result.quantityUnit} · strategy admission DENIED`;
}
