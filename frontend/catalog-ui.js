import { isDecimal } from "./model.js";
export const CATALOG_METRICS = [
  ["price", "Price"],
  ["volatility", "Volatility"],
  ["price_change_1h", "Δ 1h"],
  ["price_change_24h", "Δ 24h"],
  ["volume_24h", "Volume 24h"],
  ["turnover_24h", "Turnover 24h"],
  ["open_interest", "OI"],
  ["oi_change_1h", "OI Δ 1h"],
  ["oi_change_24h", "OI Δ 24h"],
  ["market_cap", "Market cap"],
];
export function catalogMetric(m) {
  const valid =
    m &&
    isDecimal(m.value) &&
    m.source &&
    Number.isSafeInteger(m.source_time_ms) &&
    ["OBSERVED", "STALE"].includes(m.state);
  return {
    ...m,
    value: valid ? m.value : null,
    availability: m?.state || "UNAVAILABLE",
    as_of: m?.source_time_ms,
    reason:
      m?.reason || (!valid ? "Value or source observation unavailable" : null),
  };
}
export function nextSort(sort, direction, key) {
  return {
    sort: key,
    direction:
      sort !== key || direction === "none"
        ? "asc"
        : direction === "asc"
          ? "desc"
          : "none",
  };
}
export function jobsForRow(jobs, key) {
  return jobs.filter(
    (j) =>
      `${j.request?.category || j.category}:${j.request?.symbol || j.symbol}` ===
      key,
  );
}
