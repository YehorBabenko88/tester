import test from "node:test";
import assert from "node:assert/strict";
import {
  executeRegisteredStrategy,
  validateExecutionSelection,
} from "../strategy-api.js";
const selection = {
  category: "linear",
  symbol: "BTCUSDT",
  interval: "1",
  start_ms: 0,
  end_ms: 120000,
};
const command = {
  schema_version: "tester.research-command/1",
  action: "chart.overlay",
  request_id: "00000000-0000-4000-8000-000000000001",
  expected_artifact_digest: "b".repeat(64),
  run_id: "r1",
  snapshot_id: "synthetic:s1",
  revision: "a".repeat(40),
  venue: "bybit",
  ...selection,
  cutoff_ms: 120000,
};
const complete = {
  job_id: "job1",
  state: "complete",
  chart_command: command,
  certification: {
    scope: "SYNTHETIC_ONLY",
    real_market_coverage: "NOT_PROVEN",
  },
};
const options = (fetcher) => ({
  selection,
  token: "fixture-capability",
  inputDigest: "c".repeat(64),
  requestId: command.request_id,
  fetcher,
  delay: async () => {},
});
test("registered execution posts only immutable pair identity and returns bound synthetic command", async () => {
  const calls = [];
  const results = [
    { job_id: "job1", state: "pending" },
    { job_id: "job1", state: "running" },
    complete,
  ];
  const result = await executeRegisteredStrategy(
    options(async (path, init) => {
      calls.push([path, init]);
      return new Response(JSON.stringify(results.shift()));
    }),
  );
  assert.deepEqual(result, command);
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    schema_version: "tester.research-execution/1",
    request_id: command.request_id,
    input_digest: "c".repeat(64),
  });
  assert.equal(calls[0][0], "/api/control/research-executions");
  assert.equal(calls[1][0], "/api/control/research-executions/job1");
  assert.equal(calls[0][1].headers.Authorization, "Bearer fixture-capability");
  assert.equal(calls[0][1].credentials, "omit");
});
test("missing source, token, digest or invalid range never dispatches", async () => {
  let count = 0;
  const base = options(async () => {
    count++;
    throw Error("unexpected");
  });
  for (const change of [
    { token: "" },
    { inputDigest: "bad" },
    { selection: { ...selection, symbol: null } },
    { selection: { ...selection, end_ms: 0 } },
  ])
    await assert.rejects(() =>
      executeRegisteredStrategy({ ...base, ...change }),
    );
  assert.equal(count, 0);
});
for (const code of [401, 403, 404, 409, 422, 429, 503])
  test(`HTTP ${code} is finite unavailable without private response echo`, async () => {
    await assert.rejects(
      () =>
        executeRegisteredStrategy(
          options(
            async () =>
              new Response('{"detail":"secret-token-and-dsn"}', {
                status: code,
              }),
          ),
        ),
      new RegExp(`HTTP_${code}$`),
    );
  });
test("unqualified completion, binding mismatch, version skew and wrong job never render", async () => {
  const variants = [
    {
      ...complete,
      certification: { scope: "VERIFIED", real_market_coverage: "COMPLETE" },
    },
    { ...complete, chart_command: { ...command, symbol: "ETHUSDT" } },
    { ...complete, chart_command: { ...command, schema_version: "future" } },
    { ...complete, chart_command: { ...command, snapshot_id: "real:s1" } },
    { ...complete, job_id: "other" },
  ];
  for (const value of variants) {
    let n = 0;
    await assert.rejects(() =>
      executeRegisteredStrategy(
        options(
          async () =>
            new Response(
              JSON.stringify(
                n++ ? value : { job_id: "job1", state: "pending" },
              ),
            ),
        ),
      ),
    );
  }
});
test("bounded pending and failed jobs never return a chart command", async () => {
  for (const state of ["pending", "failed", "cancelled", "denied"]) {
    let n = 0;
    await assert.rejects(() =>
      executeRegisteredStrategy({
        ...options(async () => {
          n++;
          return new Response(
            JSON.stringify({ job_id: "job1", state, reason: "private" }),
          );
        }),
        maxPolls: 2,
      }),
    );
    assert.ok(n <= 3);
  }
});
test("every supported display interval retains exact selection; unsupported source stays unavailable", () => {
  for (const interval of [
    "1",
    "3",
    "5",
    "15",
    "30",
    "60",
    "120",
    "240",
    "360",
    "720",
    "D",
    "W",
    "M",
  ])
    assert.equal(
      validateExecutionSelection({ ...selection, interval }).interval,
      interval,
    );
  assert.throws(() =>
    validateExecutionSelection({ ...selection, interval: "event" }),
  );
});
