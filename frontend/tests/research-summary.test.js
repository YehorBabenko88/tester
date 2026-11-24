import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { researchDigest } from "../research-chart.js";
import {
  validateResearchSummary,
  readResearchSummary,
} from "../research-summary.js";
const fixture = JSON.parse(
  readFileSync(new URL("./fixtures/research-summary.json", import.meta.url)),
);
const binding = {
  artifact: fixture.chart,
  artifactDigest: fixture.envelope.summary.artifact_digest,
  inputDigest: fixture.envelope.summary.input_digest,
};
async function altered(mutate) {
  const value = structuredClone(fixture.envelope);
  mutate(value.summary);
  value.summary_digest = await researchDigest(value.summary);
  return value;
}
test("genuine F summary validates exact scientific metrics and entry-window risk levels without mutation", async () => {
  const before = structuredClone(fixture);
  const summary = await validateResearchSummary(fixture.envelope, binding);
  assert.equal(summary.metrics.profit_factor, "0E+5");
  assert.match(summary.metrics.confidence.reason, /no IID confidence claim/);
  assert.deepEqual(fixture, before);
  assert.equal(summary.levels[0].price, summary.trades[0].stop_loss);
});
test("hash, input, artifact, producer, schema, source and extra fields fail closed", async () => {
  for (const mutate of [
    (s) => (s.input_digest = "a".repeat(64)),
    (s) => (s.artifact_digest = "b".repeat(64)),
    (s) => (s.run_id = "other"),
    (s) => (s.source_sha = "c".repeat(40)),
    (s) => (s.schema_version = "future"),
    (s) => (s.certification.scope = "VERIFIED"),
    (s) => (s.extra = "not_allowed"),
    (s) => (s.metrics.extra = "unknown"),
    (s) => (s.metrics.confidence.method = "IID"),
  ]) {
    await assert.rejects(() =>
      altered(mutate).then((v) => validateResearchSummary(v, binding)),
    );
  }
  await assert.rejects(() =>
    validateResearchSummary(
      { ...fixture.envelope, summary_digest: "0".repeat(64) },
      binding,
    ),
  );
});
test("risk lines require exact producer trade levels and authentic entry uncertainty window", async () => {
  for (const mutate of [
    (s) => (s.levels[0].price = "999"),
    (s) => s.levels[0].end_ms++,
    (s) => s.levels[0].known_at_ms++,
    (s) => (s.levels[0].entry_fill_id = "absent"),
    (s) => (s.levels[0].kind = "trailing"),
    (s) => (s.trades[0].exit_fill_id = "absent"),
    (s) => s.levels.push(s.levels[0]),
  ])
    await assert.rejects(() =>
      altered(mutate).then((v) => validateResearchSummary(v, binding)),
    );
});
test("nonfinite stats, unsafe timestamps, malformed quantities and unbounded arrays fail closed", async () => {
  for (const mutate of [
    (s) => (s.metrics.fees = "NaN"),
    (s) => (s.metrics.trade_count = 1.5),
    (s) => (s.trades[0].quantity = "-1"),
    (s) => (s.equity_curve[0].timestamp_ms = 2 ** 53),
    (s) => (s.brackets[0].extra = true),
    (s) => (s.equity_curve = Array(2002).fill(s.equity_curve[0])),
  ])
    await assert.rejects(() =>
      altered(mutate).then((v) => validateResearchSummary(v, binding)),
    );
});
test("authenticated summary read uses completed execution path and finite refusal without detail echo", async () => {
  let call;
  const summary = await readResearchSummary({
    ...binding,
    jobId: "job1",
    token: "fixture-token",
    fetcher: async (path, init) => {
      call = [path, init];
      return new Response(JSON.stringify(fixture.envelope));
    },
  });
  assert.equal(summary.run_id, binding.artifact.run_id);
  assert.equal(call[0], "/api/control/research-executions/job1/summary");
  assert.equal(call[1].headers.Authorization, "Bearer fixture-token");
  assert.equal(call[1].credentials, "omit");
  await assert.rejects(
    () =>
      readResearchSummary({
        ...binding,
        jobId: "job1",
        token: "fixture-token",
        fetcher: async () =>
          new Response('{"detail":"secret"}', { status: 403 }),
      }),
    /SUMMARY_HTTP_403$/,
  );
  let calls = 0;
  await assert.rejects(() =>
    readResearchSummary({
      ...binding,
      jobId: "job1",
      token: "",
      fetcher: async () => {
        calls++;
      },
    }),
  );
  assert.equal(calls, 0);
});
