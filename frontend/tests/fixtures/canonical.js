// Controlled fixtures conform to contracts/v1/market.schema.json at 9766c009.
export const START = 1704067200000;
export const provenance = [
  {
    source_kind: "bybit_archive",
    source_ref: "fixture://source",
    source_hash: null,
    ingestion_run: "fixture-run",
    timestamp_precision: "ms",
    source_timestamp: "1704067200000",
  },
];
export function candle(
  status = "UNAVAILABLE",
  reason = "source_conflict_fixture",
) {
  return {
    schema_version: "1.0",
    venue: "bybit",
    category: "linear",
    symbol: "BTCUSDT",
    timestamp_ms: START,
    provenance,
    availability: { status, reason: status === "AVAILABLE" ? null : reason },
    coverage: {
      status: status === "AVAILABLE" ? "COMPLETE" : "PARTIAL",
      start_ms: START,
      end_ms: START + 60000,
      evidence_refs: ["fixture://coverage"],
    },
    start_ms: START,
    end_ms: START + 60000,
    interval: "1",
    kind: "official_trade",
    open: "100",
    high: "101",
    low: "99",
    close: "100",
    volume: "1",
    is_closed: true,
  };
}
export function envelope(
  rows,
  status = "UNAVAILABLE",
  reason = "source_conflict_fixture",
) {
  return {
    schema_version: "1.0",
    data: rows,
    next_cursor: null,
    availability: { status, reason: status === "AVAILABLE" ? null : reason },
    coverage: {
      status: status === "AVAILABLE" ? "COMPLETE" : "PARTIAL",
      start_ms: START,
      end_ms: START + 60000,
      evidence_refs: ["fixture://coverage"],
    },
    provenance,
  };
}
