import test from "node:test";
import assert from "node:assert/strict";
import { researchGeometry } from "../research-chart.js";
const chart = {
  cutoff_ms: 3000,
  candles: [{ start_ms: 1000, end_ms: 3000, low: "90", high: "110" }],
  markers: [],
};
const levels = [
  {
    entry_fill_id: "f1",
    kind: "sl",
    price: "80.000",
    start_ms: 1000,
    end_ms: 3000,
    known_at_ms: 1000,
  },
  {
    entry_fill_id: "f1",
    kind: "tp",
    price: "120",
    start_ms: 1000,
    end_ms: 3000,
    known_at_ms: 1000,
  },
];
test("risk overlay clips authentic supplied windows and includes exact levels in display scale", () => {
  const g = researchGeometry(chart, [1500, 2500], 600, 300, levels);
  assert.ok(Array.isArray(g.levels));
  assert.equal(g.levels.length, 2);
  assert.equal(g.levels[0].source, levels[0]);
  assert.equal(g.levels[0].start_x, g.bounds.left);
  assert.equal(g.levels[0].end_x, g.bounds.right);
  assert.ok(
    g.levels.every((l) => l.y >= g.bounds.top && l.y <= g.bounds.bottom),
  );
  assert.ok(g.y(90) < g.y(80));
});
test("risk overlays never extend a supplied half-open lifetime or future-knowledge cutoff", () => {
  assert.deepEqual(
    researchGeometry(chart, [3000, 4000], 600, 300, levels).levels,
    [],
  );
  assert.deepEqual(
    researchGeometry(
      { ...chart, cutoff_ms: 999 },
      [1000, 3000],
      600,
      300,
      levels,
    ).levels,
    [],
  );
  assert.deepEqual(researchGeometry(chart, [1000, 3000], 600, 300).levels, []);
});
