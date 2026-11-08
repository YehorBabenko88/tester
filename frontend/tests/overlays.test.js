import test from "node:test";
import assert from "node:assert/strict";
import { presentOverlay } from "../overlays.js";
const scope = {
  run_id: "fixture-run",
  snapshot_id: "fixture-snapshot",
  revision: "r1",
  symbol: "BTCUSDT",
  category: "linear",
  interval: "1",
  start_ms: 1000,
  end_ms: 3000,
  cutoff_ms: 3000,
};
const fixture = () => ({
  schema_version: "overlay-presentation-fixture/1",
  evidence_mode: "SYNTHETIC_FIXTURE",
  scope: { ...scope },
  source: {
    id: "fixture-export",
    category: "OWNER_AUTHORIZED_EXPORT",
    availability: "AVAILABLE",
    permission: "GRANTED",
    evidence_ref: "fixture-only",
    revision: "r1",
  },
  coverage: "PARTIAL",
  events: [
    {
      event_id: "e1",
      classification: "DISCLOSED",
      timestamp_ms: 1500,
      known_at_ms: 2000,
      time_precision: "bar",
      time_window: { start_ms: 1000, end_ms: 2000 },
      price: "42000.000000000000001",
      order_id: "o1",
      fill_id: null,
      trade_id: null,
      evidence_ref: "fixture-event",
      fees: null,
    },
  ],
});
test("no qualified integration is inferred from a fixture", () => {
  const result = presentOverlay(fixture(), scope);
  assert.equal(result.integration_status, "NOT_INTEGRATED");
  assert.equal(result.evidence_mode, "SYNTHETIC_FIXTURE");
  assert.equal(result.coverage, "PARTIAL");
  assert.equal(result.events[0].price, "42000.000000000000001");
  assert.equal(result.events[0].trade_id, null);
  assert.deepEqual(result.events[0].time_window, {
    start_ms: 1000,
    end_ms: 2000,
  });
  assert.match(result.events[0].label, /DISCLOSED/);
  assert.match(result.events[0].label, /UNKNOWN/);
});
test("unknown, denied, revoked and absent source permission fail closed", () => {
  for (const permission of ["UNKNOWN", "DENIED", "REVOKED", undefined]) {
    const f = fixture();
    f.source.permission = permission;
    assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
    assert.deepEqual(presentOverlay(f, scope).events, []);
  }
});
test("anonymous prints cannot produce trader overlays", () => {
  const f = fixture();
  f.source.category = "ANONYMOUS_MARKET";
  assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
});
test("scope, future knowledge, decimal, duplicate and timing corruption reject whole page", () => {
  const mutations = [
    (f) => (f.scope.revision = "other"),
    (f) => (f.events[0].known_at_ms = 3001),
    (f) => (f.events[0].price = 42000),
    (f) => f.events.push({ ...f.events[0] }),
    (f) => delete f.events[0].time_window,
    (f) => (f.events[0].timestamp_ms = 3000),
    (f) => (f.source.evidence_ref = ""),
    (f) => (f.source.availability = "UNAVAILABLE"),
    (f) => (f.schema_version = "1"),
    (f) => (f.events[0].classification = "OBSERVED"),
    (f) => (f.coverage = "COMPLETE"),
  ];
  for (const mutate of mutations) {
    const f = fixture();
    mutate(f);
    assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
  }
});
test("inferred events require hypothesis provenance and retain distinct text", () => {
  const f = fixture();
  f.events[0].classification = "INFERRED";
  assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
  f.events[0].hypothesis_id = "h1";
  assert.match(presentOverlay(f, scope).events[0].label, /INFERRED hypothesis/);
});
test("bounded pages, no payload and live-like inputs stay unavailable", () => {
  assert.equal(presentOverlay(null, scope).availability, "DATA_UNAVAILABLE");
  const f = fixture();
  f.evidence_mode = "LIVE";
  assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
  f.evidence_mode = "SYNTHETIC_FIXTURE";
  f.events = Array(1001).fill(f.events[0]);
  assert.equal(presentOverlay(f, scope).availability, "DATA_UNAVAILABLE");
});
