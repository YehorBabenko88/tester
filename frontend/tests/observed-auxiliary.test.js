import test from "node:test";
import assert from "node:assert/strict";
import { bucketStart, nextBoundary } from "../time.js";
import { projectObservedAuxiliary } from "../observed-auxiliary.js";

const intervals = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];
const timestamp = Date.UTC(2024, 1, 20, 13, 0, 0);
const observed_at_utc = new Date(timestamp + 5000).toISOString();
const exactOi = "123456789.000001";
const exactFunding = "-0.0000123400";

function make(kind, interval = "1", change = {}) {
  const isOi = kind === "open_interest";
  const start_ms = bucketStart(timestamp, interval);
  const end_ms = nextBoundary(start_ms, interval);
  const params = new URLSearchParams({
    category: "linear", symbol: "BTCUSDT", limit: "200",
    ...(isOi ? { intervalTime: "5min" } : {}),
  });
  const source = {
    url: "https://api.bybit.com/v5/market/" +
      (isOi ? "open-interest" : "funding/history") + "?" + params,
    fetched_at: observed_at_utc, observed_at_utc,
    source_kind: "rest", source_timestamp: String(timestamp),
    ingestion_run: "source-run-1", hash_scope: "canonical_row_json",
    sha256: "a".repeat(64), evidence_refs: ["immutable-page-1"],
  };
  const payload = {
    value: isOi ? exactOi : exactFunding,
    unit: isOi ? "contracts_source_native" : "fraction_per_funding_event",
    publication_timestamp_ms: null,
    [isOi ? "observation_timestamp_ms" : "event_timestamp_ms"]: timestamp,
  };
  const row = {
    schema_version: "1.0", venue: "bybit", category: "linear",
    symbol: "BTCUSDT", dataset: kind, interval: isOi ? "5min" : "",
    timestamp_ms: timestamp, timestamp_precision: "ms",
    record_key: "b".repeat(64), content_hash: "c".repeat(64),
    payload, source,
    ...change,
  };
  return { row, q: { category: "linear", symbol: "BTCUSDT",
    interval, source_interval: "5min", start_ms, end_ms } };
}

for (const interval of intervals) {
  test("source-native OI and Funding preserve decimals across " + interval, () => {
    for (const kind of ["open_interest", "funding"]) {
      const { row, q } = make(kind, interval);
      const result = projectObservedAuxiliary([row], kind, q);
      assert.equal(result.state, "OBSERVED_UNVERIFIED");
      assert.equal(result.plot_rows.length, 1);
      assert.equal(result.plot_rows[0].ts, timestamp);
      assert.equal(result.plot_rows[0].value,
        kind === "open_interest" ? exactOi : exactFunding);
      assert.equal(result.original_http_attested, false);
      assert.equal(result.historical_coverage, "UNKNOWN");
      assert.equal(result.admitted_to_f, false);
      assert.equal(result.publication_known, false);
      assert.equal(result.source_interval, kind === "funding" ? "event" : "5min");
      assert.equal(result.plot_rows[0].volume, undefined);
    }
  });
}

test("rejects unverified native unit, hash, publication and event time", () => {
  for (const kind of ["open_interest", "funding"]) {
    const { row, q } = make(kind);
    for (const change of [
      { payload: { ...row.payload, value: 12.5 } },
      { payload: { ...row.payload, unit: "fabricated_asset_amount" } },
      { payload: { ...row.payload, publication_timestamp_ms: timestamp } },
      { payload: { ...row.payload, event_timestamp_ms: timestamp + 1 } },
      { content_hash: "not-a-hash" },
      { source: { ...row.source, sha256: "bad" } },
      { source: { ...row.source, source_timestamp: "0" } },
    ]) {
      assert.equal(projectObservedAuxiliary([{ ...row, ...change }], kind, q).state,
        "UNAVAILABLE");
    }
  }
});

test("wrong URL origin and OI cadence are unavailable", () => {
  const { row, q } = make("open_interest");
  for (const bad of [
    { ...row, source: { ...row.source,
      url: row.source.url.replace("api.bybit.com", "example.com") } },
    { ...row, source: { ...row.source,
      url: row.source.url.replace("intervalTime=5min", "intervalTime=1h") } },
  ]) assert.equal(projectObservedAuxiliary([bad], "open_interest", q).state,
    "UNAVAILABLE");
  assert.equal(projectObservedAuxiliary([row], "open_interest",
    { ...q, source_interval: "1m" }).reason,
    "native_oi_source_cadence_unavailable");
});

test("funding can be signed, but OI negative and late-known observations fail", () => {
  const funding = make("funding");
  assert.equal(projectObservedAuxiliary([funding.row], "funding", funding.q)
    .plot_rows[0].value, exactFunding);
  const oi = make("open_interest");
  const invalid = { ...oi.row, payload: { ...oi.row.payload, value: "-1" } };
  assert.equal(projectObservedAuxiliary([invalid], "open_interest", oi.q).state,
    "UNAVAILABLE");
  assert.equal(projectObservedAuxiliary([oi.row], "open_interest",
    { ...oi.q, information_cutoff_ms: timestamp + 1000 }).reason,
    "auxiliary_observation_knowledge_time_unknown");
});

test("empty and duplicate observations never imply full historical coverage", () => {
  const { row, q } = make("funding");
  const empty = projectObservedAuxiliary([], "funding", q);
  assert.equal(empty.state, "UNAVAILABLE");
  assert.equal(empty.historical_coverage, "UNKNOWN");
  const duplicate = projectObservedAuxiliary([row, row], "funding", q);
  assert.equal(duplicate.state, "UNAVAILABLE");
  assert.equal(duplicate.plot_rows.length, 0);
  assert.throws(() => projectObservedAuxiliary([row], "candles", q), TypeError);
});
