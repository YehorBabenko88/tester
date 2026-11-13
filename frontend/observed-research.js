// Pure projection for RESEARCH #71's source-observed output after DATA #72
// readback. No fetch, no URL creation, no simulated candles and no F admission.
import { bucketStart, nextBoundary } from "./time.js";

const METRICS_METHOD = "tester.execution-metrics/1";
const PREVIEW_VERSION = "tester.data-research-preview/2";
const DATA_VERSION = "tester-public-execution/1";
const PANELS = new Set(["footprint", "delta", "cvd", "vwap", "poc"]);
const DECIMAL = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

function exact(value, signed = false) {
  return typeof value === "string" && value.length <= 128 &&
    DECIMAL.test(value) && (signed || !value.startsWith("-"));
}
function unavailable(panel, reason) {
  return {
    panel, state: "UNAVAILABLE", reason, rows: [],
    sample_only: true, historical_coverage: "UNKNOWN",
    admitted_to_f: false, source_verified: false,
  };
}

export function projectObservedResearch(preview, panel, query) {
  if (!PANELS.has(panel)) throw TypeError("Unknown observed research panel");
  if (!preview || preview.schema_version !== PREVIEW_VERSION ||
      preview.data_contract_version !== DATA_VERSION)
    return unavailable(panel, "research_or_data_contract_not_composed");
  if (query?.interval === "M")
    return unavailable(panel, "variable_calendar_month_not_supported");
  if (query?.category !== "linear" || typeof query.symbol !== "string")
    return unavailable(panel, "linear_source_native_quantity_required");
  if (!Number.isSafeInteger(query.start_ms) ||
      !Number.isSafeInteger(query.end_ms) ||
      query.start_ms >= query.end_ms)
    return unavailable(panel, "invalid_utc_window");
  const source = preview.observed_window;
  if (preview.coverage !== "UNKNOWN" || preview.admitted_to_f !== false ||
      preview.f_input != null || preview.g_e_result != null ||
      preview.quantity_unit !== "source_native_linear_size" ||
      preview.publication_time !== "UNKNOWN" ||
      preview.persisted_dataset_revision != null ||
      preview.historical_known_at_ms != null ||
      source?.complete_market_history !== false ||
      source.historical_completeness_state !== "UNKNOWN" ||
      source.hit_bounded_event_limit !== false ||
      source.start_ms !== query.start_ms || source.end_ms !== query.end_ms)
    return unavailable(panel, "research_scope_or_visibility_unverified");
  if (preview.source_verification !== "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS")
    return unavailable(panel, "data72_original_http_receipts_not_verified");
  if (preview.status !== "OBSERVED_UNVERIFIED")
    return unavailable(panel, "no_verified_executions_in_window");
  const m = preview.metrics;
  if (!m || m.method !== METRICS_METHOD ||
      m.status !== "DERIVED_UNVERIFIED" ||
      m.admitted_to_f !== false ||
      m.cvd_prior_history_known !== false ||
      m.cvd_anchor_start_ms !== query.start_ms ||
      m.market?.venue !== "bybit" ||
      m.market.category !== query.category ||
      m.market.symbol !== query.symbol ||
      !Array.isArray(m.bars) || m.bars.length > 1000 ||
      !Array.isArray(m.reasons))
    return unavailable(panel, "research_metrics_contract_not_verified");
  const rows = [];
  let previous = -1;
  for (const bar of m.bars) {
    if (!Number.isSafeInteger(bar.start_ms) ||
        !Number.isSafeInteger(bar.end_ms) ||
        bar.start_ms < query.start_ms || bar.end_ms > query.end_ms ||
        bar.start_ms <= previous ||
        bucketStart(bar.start_ms, query.interval) !== bar.start_ms ||
        nextBoundary(bar.start_ms, query.interval) !== bar.end_ms ||
        bar.coverage_certified !== false ||
        bar.delta_complete !== false ||
        bar.vwap_volume_coverage_certified !== false ||
        bar.poc_volume_coverage_certified !== false ||
        !Array.isArray(bar.evidence_refs) ||
        !bar.evidence_refs.every(x => typeof x === "string" && x) ||
        !["volume", "buy_volume", "sell_volume", "unknown_side_volume"]
          .every(field => exact(bar[field])) ||
        !["delta_known", "cvd_known"].every(field => exact(bar[field], true)) ||
        (bar.vwap != null && !exact(bar.vwap)) ||
        (bar.poc != null && !exact(bar.poc)) ||
        !Array.isArray(bar.footprint) || bar.footprint.length > 1000)
      return unavailable(panel, "observed_bar_decimal_utc_or_provenance_invalid");
    previous = bar.start_ms;
    const ts = bar.start_ms;
    if (panel === "footprint") {
      const levels = [];
      for (const level of bar.footprint) {
        if (!exact(level.price) || !exact(level.volume) ||
            !exact(level.buy_volume) || !exact(level.sell_volume) ||
            !exact(level.unknown_side_volume))
          return unavailable(panel, "observed_footprint_level_invalid");
        levels.push({
          price: level.price, volume: level.volume,
          taker_buy: level.buy_volume, taker_sell: level.sell_volume,
          unknown_side_volume: level.unknown_side_volume,
        });
      }
      rows.push({ ts, levels, trade_count: bar.trade_count });
    } else if (panel === "delta") {
      rows.push({ ts, value: bar.delta_known, kind: "known_taker_delta" });
    } else if (panel === "cvd") {
      rows.push({ ts, value: bar.cvd_known, kind: "window_anchored_known_cvd" });
    } else if (panel === "vwap" || panel === "poc") {
      const value = bar[panel];
      if (value != null) rows.push({ ts, value, kind: "observed_sample_" + panel });
    }
  }
  if (!rows.length)
    return unavailable(panel, "no_observed_sample_metric_values");
  return {
    panel, state: "OBSERVED_UNVERIFIED",
    reason: "historical_visibility_not_proven",
    rows, sample_only: true, historical_coverage: "UNKNOWN",
    admitted_to_f: false, source_verified: true,
    source_contract: DATA_VERSION, metrics_method: METRICS_METHOD,
    observed_sample_fingerprint: preview.observed_sample_fingerprint,
    source_receipts_digest: preview.data_source_receipts_digest,
    display_warning: "Observed public executions only; no complete history, maker/Bid-Ask inference or trading admission",
  };
}
