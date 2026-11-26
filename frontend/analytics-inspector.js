import { classifyAnalyticsReadback } from "./analytics-contract.js";

// This is a bounded, read-only capability inspector, NOT a derived data source.
// An API capability advertisement never grants F admission or a live feed.
const FAMILIES = new Set([
  "hourly_range_percent", "rolling_60_minute_range_percent",
  "realized_volatility", "reconcile_minute_volume", "compare_candles",
  "vwap", "footprint", "delta", "cvd", "poc",
]);
const EXECUTION_PANELS = new Set(["vwap", "footprint", "delta", "cvd", "poc"]);
const SOURCE_LIMIT_MS = 7 * 86400000;
const MAX_TEXT = 240;

function limited(value) {
  if (typeof value !== "string" || !value) return "UNKNOWN";
  return value.length <= MAX_TEXT ? value : value.slice(0, MAX_TEXT) + "…";
}
export function inspectorScope(raw) {
  if (!raw || !["linear", "inverse", "spot"].includes(raw.category))
    throw Error("Select a supported market category");
  const symbol = raw.symbol?.trim().toUpperCase();
  if (!symbol || !/^[A-Z0-9_-]{1,40}$/.test(symbol))
    throw Error("Select a valid market instrument");
  if (!FAMILIES.has(raw.metric)) throw Error("Unsupported analytics metric");
  if (!/^(1|3|5|15|30|60|120|240|360|720|D|W|M)$/.test(raw.interval ?? ""))
    throw Error("Unsupported timeframe");
  const parseUtc = (value) => typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)
    ? Date.parse(value + "Z") : NaN;
  const start_ms = parseUtc(raw.start), end_ms = parseUtc(raw.end);
  if (!Number.isSafeInteger(start_ms) || !Number.isSafeInteger(end_ms) ||
      start_ms < 0 || end_ms <= start_ms ||
      end_ms - start_ms > SOURCE_LIMIT_MS ||
      new Date(start_ms).toISOString().slice(0, 16) !== raw.start ||
      new Date(end_ms).toISOString().slice(0, 16) !== raw.end)
    throw Error("Choose a valid bounded UTC interval (maximum 7 days)");
  return Object.freeze({
    category: raw.category, symbol, metric: raw.metric,
    interval: raw.interval, start_ms, end_ms,
  });
}
export function inspectCapabilityPayload(data, scope) {
  const deny = (reason) => ({
    state: "UNVERIFIED", reason,
    quantityUnit: "UNKNOWN", method: "UNKNOWN",
    historicalCoverage: "UNKNOWN", sourceObservation: "UNKNOWN",
    strategyAdmissionAllowed: false, requestAllowed: false,
  });
  if (!data || typeof data !== "object" || Array.isArray(data))
    return deny("invalid_capability_payload");
  if (data.protocol_version !== "tester.ui-capabilities/1")
    return deny("capability_contract_unverified");
  if (!EXECUTION_PANELS.has(scope.metric))
    return deny("analytics_quality_route_not_registered");
  const classification = classifyAnalyticsReadback(data, scope.category, scope.metric);
  const source = data.execution_source_evidence;
  return {
    ...deny(classification.reason),
    quantityUnit: data.source_contracts?.observed_execution_metrics
      ?.quantity_unit?.[scope.category] ?? "UNKNOWN",
    method: data.source_contracts?.observed_execution_metrics
      ?.contract_version ?? "UNKNOWN",
    // An observed recent sample never proves completeness of historical data.
    historicalCoverage: "UNKNOWN",
    sourceObservation: typeof source?.status === "string"
      ? source.status : "UNKNOWN",
  };
}

export function mountAnalyticsInspector(root, { getScope, fetchCapabilities }) {
  const form = root.querySelector("#analytics-inspector-form");
  const status = root.querySelector("#analytics-inspector-status");
  const details = root.querySelector("#analytics-inspector-evidence");
  const button = root.querySelector("#analytics-inspector-submit");
  const select = root.querySelector("#analytics-inspector-metric");
  if (!form || !status || !details || !button || !select) throw Error("Missing inspector UI");
  let generation = 0;
  let abort = null;
  const render = (message, evidence = "") => {
    status.textContent = message;
    details.textContent = evidence;
  };
  function reset() {
    generation++;
    abort?.abort();
    abort = null;
    button.disabled = false;
    render("UNVERIFIED · No verified analytics readback selected.");
  }
  async function inspect() {
    const token = ++generation;
    abort?.abort();
    abort = new AbortController();
    button.disabled = true;
    try {
      const scope = inspectorScope({ ...getScope(), metric: select.value });
      render("Checking canonical contract; no strategy admission…");
      // This is the only network request. Never probe a speculative /api/analytics path.
      const response = await fetchCapabilities({
        category: scope.category, symbol: scope.symbol,
        interval: scope.interval, start_ms: scope.start_ms,
        end_ms: scope.end_ms, limit: 100,
      }, abort.signal);
      if (token !== generation || abort.signal.aborted) return;
      // Recheck all inputs after the asynchronous boundary.
      const current = inspectorScope({ ...getScope(), metric: select.value });
      if (JSON.stringify(scope) !== JSON.stringify(current)) {
        render("UNVERIFIED · Selection changed before source evidence arrived.");
        return;
      }
      const result = inspectCapabilityPayload(response?.data, scope);
      render(
        `${result.state} · ${limited(result.reason)} · category ${scope.category} · ${scope.symbol} · ${scope.interval} · unit ${limited(result.quantityUnit)} · math ${limited(result.method)} · source ${limited(result.sourceObservation)} · historical coverage ${result.historicalCoverage} · strategy admission DENIED`,
        `UTC [${scope.start_ms}, ${scope.end_ms}) · ${scope.metric} · ${result.requestAllowed ? "readback gated" : "NO analytics data request sent"} · missing != zero · simulated != source-backed`,
      );
    } catch (error) {
      if (token === generation)
        render("DATA_UNAVAILABLE · " + limited(error?.message) + " · no analytics request sent.");
    } finally {
      if (token === generation) {
        abort = null;
        button.disabled = false;
      }
    }
  }
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void inspect();
  });
  return { inspect, reset };
}
