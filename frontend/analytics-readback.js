// Separate optional source-backed observation viewer.
// A source sample is never complete historical coverage or strategy permission.
const READBACK = "/api/analytics/readback";
const CONTRACT = "tester.analytics-readback/1";
const METHOD = "tester.execution-metrics/2";
const MAX_BYTES = 512 * 1024;
const MAX_BARS = 100;
const MAX_RANGE_MS = 7 * 86400000;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;
const DECIMAL = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const UNIT = { linear: "base", inverse: "quote" };
const SOURCE_UNIT = {
  linear: "source_native_linear_size",
  inverse: "source_native_inverse_contract",
};

function utcMs(value) {
  if (typeof value !== "string" || !UTC.test(value)) throw Error("invalid_utc");
  const ms = Date.parse(value + "Z");
  if (!Number.isSafeInteger(ms) || ms < 0 ||
      new Date(ms).toISOString().slice(0, 16) !== value)
    throw Error("invalid_utc");
  return ms;
}
export function observedScope(selection) {
  if (!selection || !Object.hasOwn(UNIT, selection.category)) throw Error("unsupported_market");
  const symbol = selection.symbol?.trim().toUpperCase();
  if (!/^[A-Z0-9]{2,40}$/.test(symbol ?? "")) throw Error("invalid_market_symbol");
  if (!/^(1|3|5|15|30|60|120|240|360|720)$/.test(selection.interval ?? ""))
    throw Error("unsupported_observation_interval");
  const interval_ms = Number(selection.interval) * 60000;
  const start_ms = utcMs(selection.start);
  const end_ms = utcMs(selection.end);
  const as_of_ms = utcMs(selection.as_of);
  if (end_ms <= start_ms || end_ms - start_ms > MAX_RANGE_MS ||
      start_ms % interval_ms !== 0 || end_ms % interval_ms !== 0 ||
      end_ms > as_of_ms || (end_ms - start_ms) / interval_ms > MAX_BARS)
    throw Error("observation_window_not_bounded_or_closed");
  const tick_size = selection.tick_size;
  if (typeof tick_size !== "string" || tick_size.length > 32 ||
      !DECIMAL.test(tick_size) || !/[1-9]/.test(tick_size))
    throw Error("explicit_positive_tick_size_required");
  return Object.freeze({
    category: selection.category, symbol,
    start_ms, end_ms, interval_ms, tick_size, as_of_ms, limit: 99,
  });
}
export function validatedObservedReadback(payload, scope) {
  if (payload?.schema_version !== "1.0" || !payload.data ||
      typeof payload.data !== "object" || Array.isArray(payload.data))
    throw Error("unqualified_observation_envelope");
  const data = payload.data;
  if (JSON.stringify(data).length > MAX_BYTES) throw Error("observation_exceeds_budget");
  if (data.schema_version !== CONTRACT || data.status !== "OBSERVED_UNVERIFIED" ||
      data.method !== METHOD || data.admitted_to_f !== false ||
      data.quality?.status !== "UNVERIFIED" ||
      data.quality?.coverage !== "UNKNOWN" ||
      data.quality?.admitted_to_f !== false ||
      data.metrics?.method !== METHOD ||
      data.metrics?.status !== "DERIVED_UNVERIFIED" ||
      data.metrics?.admitted_to_f !== false ||
      data.metrics?.quantity_unit !== UNIT[scope.category] ||
      data.quantity_unit !== UNIT[scope.category] ||
      data.source_quantity_unit !== SOURCE_UNIT[scope.category] ||
      data.source?.publication_time !== "UNKNOWN" ||
      data.market?.venue !== "bybit" ||
      data.market?.category !== scope.category ||
      data.market?.symbol !== scope.symbol ||
      data.window?.start_ms !== scope.start_ms ||
      data.window?.end_ms !== scope.end_ms ||
      data.window?.interval_ms !== scope.interval_ms ||
      data.window?.as_of_ms !== scope.as_of_ms ||
      !Array.isArray(data.metrics?.bars))
    throw Error("source_observation_contract_mismatch");
  const bars = data.metrics.bars;
  if (!bars.length || bars.length > MAX_BARS ||
      bars.length !== (scope.end_ms - scope.start_ms) / scope.interval_ms)
    throw Error("observation_bars_incomplete_or_oversized");
  for (const [index, bar] of bars.entries()) {
    if (!bar || bar.start_ms !== scope.start_ms + index * scope.interval_ms ||
        bar.end_ms !== bar.start_ms + scope.interval_ms ||
        typeof bar.volume !== "string" || !DECIMAL.test(bar.volume) ||
        !Object.hasOwn(bar, "vwap") ||
        (bar.vwap !== null && (typeof bar.vwap !== "string" || !DECIMAL.test(bar.vwap))))
      throw Error("unqualified_observation_bar");
  }
  return data;
}
export function mountObservedReadback(root, { getScope, fetchCapabilities, fetchReadback }) {
  const form = root.querySelector("#analytics-readback-form");
  const status = root.querySelector("#analytics-readback-status");
  const details = root.querySelector("#analytics-readback-data");
  const asOf = root.querySelector("#analytics-readback-as-of");
  const tick = root.querySelector("#analytics-readback-tick");
  const submit = root.querySelector("#analytics-readback-submit");
  if (!form || !status || !details || !asOf || !tick || !submit)
    throw Error("missing_readback_controls");
  if (!asOf.value) asOf.value = new Date().toISOString().slice(0, 16);
  let generation = 0;
  let abort = null;
  const reset = () => {
    generation++;
    abort?.abort();
    abort = null;
    submit.disabled = false;
    status.textContent = "UNVERIFIED · No qualified observation loaded";
    details.textContent = "";
  };
  const inspect = async () => {
    const token = ++generation;
    abort?.abort();
    const controller = new AbortController();
    abort = controller;
    submit.disabled = true;
    details.textContent = "";
    try {
      const selection = () => ({ ...getScope(), as_of: asOf.value, tick_size: tick.value });
      const scope = observedScope(selection());
      status.textContent = "Checking source route and bounded readback…";
      const features = (await fetchCapabilities(scope, controller.signal))?.data?.features;
      if (token !== generation || controller.signal.aborted) return;
      if (features?.analytics_readback?.enabled !== true ||
          features.analytics_readback.resource !== READBACK ||
          features.analytics_readback.status !== "OBSERVED_ONLY_NO_F_ADMISSION")
        throw Error("verified_analytics_route_not_registered");
      const payload = await fetchReadback(scope, controller.signal);
      if (token !== generation || controller.signal.aborted) return;
      if (JSON.stringify(scope) !== JSON.stringify(observedScope(selection()))) {
        reset();
        return;
      }
      const data = validatedObservedReadback(payload, scope);
      status.textContent = `OBSERVED_UNVERIFIED · ${data.market.symbol} · ${data.quantity_unit} · ${data.method} · historical coverage UNKNOWN · strategy admission DENIED`;
      const lines = data.metrics.bars.slice(0, 20).map(bar =>
        `${new Date(bar.start_ms).toISOString()} · observed volume ${bar.volume} · sample VWAP ${bar.vwap ?? "UNKNOWN"}`);
      details.textContent = `Original source: ${data.source_quantity_unit}; observed sample only, never complete historical evidence.\n` +
        lines.join("\n") + (data.metrics.bars.length > 20 ? "\nAdditional sample bars omitted from display." : "");
    } catch (error) {
      if (token === generation && !controller.signal.aborted) {
        status.textContent = `DATA_UNAVAILABLE · ${String(error?.message || "unverified").slice(0, 120)} · no strategy admission`;
      }
    } finally {
      if (token === generation) {
        submit.disabled = false;
        abort = null;
      }
    }
  };
  form.addEventListener("submit", event => {
    event.preventDefault();
    void inspect();
  });
  asOf.addEventListener("input", reset);
  tick.addEventListener("input", reset);
  return { inspect, reset };
}
