import test from "node:test";
import assert from "node:assert/strict";
import { bucketStart, nextBoundary } from "../time.js";
import { projectObservedResearch } from "../observed-research.js";

const INTERVALS = ["1", "3", "5", "15", "30", "60", "120", "240",
                   "360", "720", "D", "W", "M"];
const PRICE = "101.250000000000000000000001";
const QUANTITY = "0.000000000000000000000001";

function fixture(interval = "1") {
  const start_ms = bucketStart(Date.UTC(2024, 1, 20, 13, 0), interval);
  const end_ms = nextBoundary(start_ms, interval);
  const scope = { category: "linear", symbol: "BTCUSDT", interval, start_ms, end_ms };
  const bar = {
    start_ms, end_ms, trade_count: 1,
    volume: QUANTITY, buy_volume: QUANTITY, sell_volume: "0",
    unknown_side_volume: "0", delta_known: QUANTITY, cvd_known: QUANTITY,
    side_classification_complete: true, delta_complete: false,
    coverage_certified: false, vwap_volume_coverage_certified: false,
    poc_volume_coverage_certified: false,
    vwap: PRICE, poc: "101.25",
    footprint: [{
      price: PRICE, volume: QUANTITY, buy_volume: QUANTITY,
      sell_volume: "0", unknown_side_volume: "0",
    }],
    evidence_refs: ["sha256:original-http-row-proof"],
  };
  const preview = {
    schema_version: "tester.data-research-preview/2",
    data_contract_version: "tester-public-execution/1",
    status: "OBSERVED_UNVERIFIED",
    coverage: "UNKNOWN", quantity_unit: "source_native_linear_size",
    publication_time: "UNKNOWN",
    source_verification: "DATA72_PERSISTED_HTTP_ROW_PAGE_RECEIPTS",
    observed_window: {
      start_ms, end_ms, complete_market_history: false,
      historical_completeness_state: "UNKNOWN",
      hit_bounded_event_limit: false,
    },
    persisted_dataset_revision: null, historical_known_at_ms: null,
    admitted_to_f: false, f_input: null, g_e_result: null,
    observed_sample_fingerprint: "a".repeat(64),
    data_source_receipts_digest: "b".repeat(64),
    data_execution_digest: "c".repeat(64),
    metrics_digest: "d".repeat(64),
    metrics: {
      method: "tester.execution-metrics/1",
      status: "DERIVED_UNVERIFIED", admitted_to_f: false,
      cvd_prior_history_known: false, cvd_anchor_start_ms: start_ms,
      market: { venue: "bybit", category: "linear", symbol: "BTCUSDT" },
      bars: [bar], reasons: ["no_historical_visibility_proof"],
    },
  };
  return { scope, preview, bar };
}

for (const interval of INTERVALS) {
  test("DATA72→RESEARCH71 samples preserve UTC+Decimal on " + interval, () => {
    const { scope, preview } = fixture(interval);
    for (const panel of ["footprint", "delta", "cvd", "vwap", "poc"]) {
      const out = projectObservedResearch(preview, panel, scope);
      if (interval === "M") {
        assert.equal(out.state, "UNAVAILABLE");
        assert.equal(out.reason, "variable_calendar_month_not_supported");
        assert.deepEqual(out.rows, []);
        continue;
      }
      assert.equal(out.state, "OBSERVED_UNVERIFIED");
      assert.equal(out.admitted_to_f, false);
      assert.equal(out.historical_coverage, "UNKNOWN");
      assert.equal(out.source_verified, true);
      assert.equal(out.rows[0].ts, scope.start_ms);
    }
    if (interval !== "M") {
      assert.equal(projectObservedResearch(preview, "vwap", scope).rows[0].value, PRICE);
      assert.equal(projectObservedResearch(preview, "delta", scope).rows[0].value, QUANTITY);
      const levels = projectObservedResearch(preview, "footprint", scope).rows[0].levels;
      assert.equal(levels[0].price, PRICE);
      assert.equal(levels[0].taker_buy, QUANTITY);
      assert.equal(levels[0].taker_sell, "0");
    }
  });
}

test("unknown taker side never fabricates positive/negative delta or bid/ask", () => {
  const { scope, preview, bar } = fixture();
  bar.unknown_side_volume = QUANTITY;
  bar.side_classification_complete = false;
  bar.delta_known = "0";
  bar.cvd_known = "0";
  bar.footprint[0].buy_volume = "0";
  bar.footprint[0].unknown_side_volume = QUANTITY;
  const delta = projectObservedResearch(preview, "delta", scope);
  assert.equal(delta.rows[0].value, "0");
  assert.equal(delta.state, "OBSERVED_UNVERIFIED");
  const footprint = projectObservedResearch(preview, "footprint", scope);
  assert.equal(footprint.rows[0].levels[0].unknown_side_volume, QUANTITY);
  assert.equal(footprint.rows[0].levels[0].taker_buy, "0");
  assert.equal("bid_volume" in footprint.rows[0].levels[0], false);
});

test("no composed DATA source, unknown history and version skew fail closed", () => {
  const { scope, preview } = fixture();
  for (const wrong of [
    { data_contract_version: "legacy" },
    { schema_version: "tester.data-research-preview/1" },
    { source_verification: "CALLER_UNATTESTED" },
    { metrics_digest: "not-a-hash" },
    { coverage: "COMPLETE" },
    { persisted_dataset_revision: "fake-revision" },
    { admitted_to_f: true },
    { historical_known_at_ms: scope.end_ms },
    { observed_window: { ...preview.observed_window, hit_bounded_event_limit: true } },
  ]) {
    const result = projectObservedResearch({ ...preview, ...wrong }, "vwap", scope);
    assert.equal(result.state, "UNAVAILABLE");
    assert.equal(result.rows.length, 0);
  }
});

test("bad decimal and misaligned source timestamps do not get a plot", () => {
  const { scope, preview } = fixture();
  for (const bad of [
    { volume: "1e-9" }, { vwap: 101.25 }, { delta_known: Number(QUANTITY) },
    { start_ms: scope.start_ms + 1 },
    { end_ms: scope.end_ms - 1 },
    { coverage_certified: true },
  ]) {
    const altered = { ...preview, metrics: {
      ...preview.metrics, bars: [{ ...preview.metrics.bars[0], ...bad }],
    }};
    assert.equal(projectObservedResearch(altered, "vwap", scope).state, "UNAVAILABLE");
  }
});
test("unknown panels throw without discovering speculative HTTP routes", () => {
  const { scope, preview } = fixture();
  assert.throws(() => projectObservedResearch(preview, "funding", scope), TypeError);
  assert.throws(() => projectObservedResearch(preview, "open_interest", scope), TypeError);
});
