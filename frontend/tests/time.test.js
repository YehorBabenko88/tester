import { test } from "node:test";
import assert from "node:assert/strict";
import { bucketStart, nextBoundary, shiftRange, bucketWidth } from "../time.js";
test("UTC months respect leap year and year boundary", () => {
  const feb = Date.UTC(2024, 1, 15, 12);
  assert.equal(bucketStart(feb, "M"), Date.UTC(2024, 1, 1));
  assert.equal(nextBoundary(feb, "M"), Date.UTC(2024, 2, 1));
  assert.equal(bucketWidth(feb, "M"), 29 * 86400000);
  assert.deepEqual(
    shiftRange([Date.UTC(2024, 0, 1), Date.UTC(2024, 1, 1)], "M", 1),
    [Date.UTC(2024, 1, 1), Date.UTC(2024, 2, 1)],
  );
  assert.deepEqual(
    shiftRange([Date.UTC(2024, 11, 1), Date.UTC(2025, 0, 1)], "M", 1),
    [Date.UTC(2025, 0, 1), Date.UTC(2025, 1, 1)],
  );
});
test("UTC weeks start Monday and ignore local DST", () => {
  assert.equal(
    bucketStart(Date.UTC(2024, 2, 31, 20), "W"),
    Date.UTC(2024, 2, 25),
  );
  assert.equal(
    nextBoundary(Date.UTC(2024, 2, 31, 20), "W"),
    Date.UTC(2024, 3, 1),
  );
});
test("month-end clamping never collapses a positive half-open range", () => {
  assert.deepEqual(
    shiftRange([Date.UTC(2024, 0, 30), Date.UTC(2024, 0, 31)], "M", 1),
    [Date.UTC(2024, 1, 29), Date.UTC(2024, 2, 1)],
  );
});
