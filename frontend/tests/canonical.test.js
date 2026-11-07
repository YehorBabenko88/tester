import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import Ajv from "ajv/dist/2020.js";
import formats from "ajv-formats";
import { candle, envelope, START } from "./fixtures/canonical.js";
import { adaptSeries } from "../series.js";
import { metric } from "../model.js";
const schema = JSON.parse(
  fs.readFileSync(
    new URL("./fixtures/market-9766c009.schema.json", import.meta.url),
  ),
);
const ajv = new Ajv({ strict: false });
formats(ajv);
ajv.addSchema(schema);
const validate = (kind) => ajv.getSchema(schema.$id + "#/$defs/" + kind);
for (const status of ["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNVERIFIED"])
  test(
    "pinned canonical " + status + " fixture and metadata survive adaptation",
    () => {
      const row = candle(status),
        payload = envelope([row], status);
      assert.ok(
        validate("candle")(row),
        JSON.stringify(validate("candle").errors),
      );
      assert.ok(validate("api_response")(payload));
      const result = adaptSeries(payload, "candles", {
        start_ms: START,
        end_ms: START + 60000,
      });
      assert.equal(result.availability, status);
      assert.equal(
        result.renderable,
        ["AVAILABLE", "PARTIAL"].includes(status),
      );
      assert.deepEqual(result.provenance, payload.provenance);
      assert.deepEqual(result.coverage[0], payload.coverage);
      if (status !== "AVAILABLE")
        assert.match(result.reason, /source_conflict_fixture/);
      if (!result.renderable) assert.deepEqual(result.rows, []);
    },
  );
test("row unavailability cannot be upgraded by an AVAILABLE envelope", () => {
  const result = adaptSeries(
    envelope([candle("UNAVAILABLE")], "AVAILABLE"),
    "candles",
    { start_ms: START, end_ms: START + 60000 },
  );
  assert.equal(result.availability, "UNAVAILABLE");
  assert.equal(result.renderable, false);
});
test("nested metric unavailability retains its specific reason and structured sources", () => {
  const m = metric({
    ...candle("UNAVAILABLE", "missing_side_fixture"),
    value: null,
  });
  assert.equal(m.reason, "missing_side_fixture");
  assert.equal(m.availability, "UNAVAILABLE");
  assert.equal(m.value, null);
});
