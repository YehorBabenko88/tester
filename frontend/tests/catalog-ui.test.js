import test from "node:test";
import assert from "node:assert/strict";
import { catalogMetric, nextSort, jobsForRow } from "../catalog-ui.js";
test("catalog metrics retain exact real observation and stale state", () => {
  const m = catalogMetric({
    value: "1.200000000000001",
    state: "STALE",
    source: "Bybit",
    observed_at: 1704067200,
    source_time_ms: 1704067199000,
  });
  assert.equal(m.value, "1.200000000000001");
  assert.equal(m.availability, "STALE");
  assert.equal(m.as_of, 1704067199000);
  assert.equal(
    catalogMetric({ value: "0", state: "UNKNOWN", source: "Bybit" }).value,
    null,
  );
});
test("headers cycle ascending descending and unsorted", () => {
  assert.deepEqual(nextSort("price", "none", "price"), {
    sort: "price",
    direction: "asc",
  });
  assert.deepEqual(nextSort("price", "asc", "price"), {
    sort: "price",
    direction: "desc",
  });
  assert.deepEqual(nextSort("price", "desc", "price"), {
    sort: "price",
    direction: "none",
  });
});
test("multiple persistent jobs belong to exact category and symbol", () => {
  const jobs = [
    { id: "a", request: { category: "linear", symbol: "BTC" } },
    { id: "b", request: { category: "linear", symbol: "BTC" } },
    { id: "c", request: { category: "inverse", symbol: "BTC" } },
  ];
  assert.deepEqual(
    jobsForRow(jobs, "linear:BTC").map((x) => x.id),
    ["a", "b"],
  );
});
