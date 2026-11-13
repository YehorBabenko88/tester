// DATA #72 auxiliary observation projection only: no fetch and no complete
// history or original-HTTP proof claims. Only source-native observations.
const DECIMAL = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;
const SHA = /^[a-f0-9]{64}$/;
const CADENCES = new Set(["5min", "15min", "30min", "1h", "4h", "1d"]);
const KNOWN = new Set(["open_interest", "funding"]);

function blocked(kind, reason) {
  return {
    kind, state: "UNAVAILABLE", reason, plot_rows: [],
    historical_coverage: "UNKNOWN", original_http_attested: false,
    publication_known: false, admitted_to_f: false,
  };
}
const numberText = (s, signed) =>
  typeof s === "string" && s.length <= 128 && DECIMAL.test(s) &&
  (signed || !s.startsWith("-"));

export function projectObservedAuxiliary(records, kind, q) {
  if (!KNOWN.has(kind)) throw TypeError("Unknown auxiliary market series");
  if (!Array.isArray(records) || records.length === 0 || records.length > 1000)
    return blocked(kind, "auxiliary_observations_not_available");
  if (!q || !["linear", "inverse"].includes(q.category) ||
      !/^[A-Z0-9]{2,40}$/.test(q.symbol || "") ||
      !Number.isSafeInteger(q.start_ms) || !Number.isSafeInteger(q.end_ms) ||
      q.start_ms >= q.end_ms)
    return blocked(kind, "invalid_auxiliary_market_scope");
  const isOi = kind === "open_interest";
  const sourceInterval = q.source_interval;
  if (isOi && !CADENCES.has(sourceInterval))
    return blocked(kind, "native_oi_source_cadence_unavailable");
  const expectedPath = isOi ? "/v5/market/open-interest" :
    "/v5/market/funding/history";
  const expectedUnit = isOi ? "contracts_source_native" :
    "fraction_per_funding_event";
  const unique = new Set(), plot_rows = [], evidence_refs = [];
  let previous = -1;
  for (const record of records) {
    const timestamp = record?.timestamp_ms;
    const payload = record?.payload, src = record?.source;
    if (record?.schema_version !== "1.0" ||
        record.venue !== "bybit" || record.category !== q.category ||
        record.symbol !== q.symbol || record.dataset !== kind ||
        record.interval !== (isOi ? sourceInterval : "") ||
        record.timestamp_precision !== "ms" ||
        !Number.isSafeInteger(timestamp) ||
        timestamp < q.start_ms || timestamp >= q.end_ms ||
        timestamp <= previous ||
        !SHA.test(record.content_hash || "") ||
        !SHA.test(record.record_key || "") ||
        !payload || payload.unit !== expectedUnit ||
        !numberText(payload.value, !isOi) ||
        payload.publication_timestamp_ms !== null ||
        payload[isOi ? "observation_timestamp_ms" : "event_timestamp_ms"] !== timestamp ||
        !src || src.source_kind !== "rest" ||
        src.hash_scope !== "canonical_row_json" ||
        !SHA.test(src.sha256 || "") ||
        src.source_timestamp !== String(timestamp) ||
        typeof src.ingestion_run !== "string" || !src.ingestion_run ||
        !Array.isArray(src.evidence_refs) || !src.evidence_refs.length ||
        !src.evidence_refs.every(x => typeof x === "string" && x.length > 0))
      return blocked(kind, "auxiliary_decimal_provenance_or_utc_unverified");
    let url;
    try { url = new URL(src.url); }
    catch { return blocked(kind, "auxiliary_source_url_unverified"); }
    if (url.protocol !== "https:" || url.hostname !== "api.bybit.com" ||
        url.port || url.username || url.password || url.hash ||
        url.pathname !== expectedPath ||
        url.searchParams.get("category") !== q.category ||
        url.searchParams.get("symbol") !== q.symbol ||
        (isOi && url.searchParams.get("intervalTime") !== sourceInterval))
      return blocked(kind, "auxiliary_source_url_unverified");
    const observed = src.observed_at_utc;
    const observedMs = typeof observed === "string" ? Date.parse(observed) : NaN;
    if (!Number.isSafeInteger(observedMs) || observedMs < timestamp ||
        (q.information_cutoff_ms != null && observedMs > q.information_cutoff_ms))
      return blocked(kind, "auxiliary_observation_knowledge_time_unknown");
    if (unique.has(record.record_key)) return blocked(kind, "duplicate_auxiliary_observation");
    unique.add(record.record_key);
    previous = timestamp;
    plot_rows.push({ ts: timestamp, value: payload.value, kind:
      isOi ? "source_native_open_interest" : "funding_event_rate" });
    evidence_refs.push(...src.evidence_refs);
  }
  return {
    kind, state: "OBSERVED_UNVERIFIED",
    reason: "original_http_page_receipt_not_revalidated_for_chart",
    plot_rows, observed_records: plot_rows.length,
    evidence_refs: [...new Set(evidence_refs)],
    source_unit: expectedUnit,
    source_interval: isOi ? sourceInterval : "event",
    historical_coverage: "UNKNOWN", original_http_attested: false,
    publication_known: false, admitted_to_f: false,
    display_warning: "Source-native observed samples only; no historical completeness or certified original HTTP readback",
  };
}
