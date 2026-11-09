import test from "node:test";
import assert from "node:assert/strict";
import { mountResearchViewer } from "../research-view.js";
import { researchDigest } from "../research-chart.js";
const fixture = () => ({
  schema_version: "tester.research-chart/2",
  execution_mode: "SIMULATED",
  synthetic: true,
  coverage: "complete",
  source_ids: ["synthetic:fixture"],
  venue: "bybit",
  run_id: "r1",
  snapshot_id: "synthetic:s1",
  revision: "a".repeat(40),
  symbol: "BTCUSDT",
  category: "linear",
  interval: "1",
  start_ms: 0,
  end_ms: 120000,
  cutoff_ms: 120000,
  candles: [
    {
      start_ms: 0,
      end_ms: 60000,
      open: "10",
      high: "12",
      low: "9",
      close: "11",
    },
    {
      start_ms: 60000,
      end_ms: 120000,
      open: "11",
      high: "12",
      low: "9",
      close: "10",
    },
  ],
  markers: [
    {
      fill_id: "f1",
      order_id: "o1",
      position_id: "p1",
      closed_trade_id: null,
      timestamp_ms: 0,
      known_at_ms: 60000,
      window_start_ms: 0,
      window_end_ms: 60000,
      time_precision: "bar",
      role: "entry",
      side: "buy",
      price: "10.000",
    },
  ],
});
function root() {
  const node = () => ({
    dataset: {},
    textContent: "",
    children: [],
    listeners: {},
    clientWidth: 700,
    clientHeight: 350,
    width: 700,
    height: 350,
    addEventListener(k, fn) {
      this.listeners[k] = fn;
    },
    append(n) {
      this.children.push(n);
    },
    replaceChildren() {
      this.children = [];
    },
    setAttribute() {},
    getContext() {
      return new Proxy(
        { measureText: () => ({ width: 10 }) },
        { get: (target, key) => target[key] || (() => {}) },
      );
    },
  });
  const nodes = new Map();
  const root = {
    querySelector(s) {
      if (!nodes.has(s)) nodes.set(s, node());
      return nodes.get(s);
    },
  };
  globalThis.ResizeObserver = class {
    observe() {}
  };
  globalThis.document = { createElement: node };
  return root;
}
const command = (artifact, digest) =>
  Object.fromEntries([
    ...Object.keys(artifact)
      .filter((k) =>
        [
          "run_id",
          "snapshot_id",
          "revision",
          "venue",
          "category",
          "symbol",
          "interval",
          "start_ms",
          "end_ms",
          "cutoff_ms",
        ].includes(k),
      )
      .map((k) => [k, artifact[k]]),
    ["schema_version", "tester.research-command/1"],
    ["action", "chart.overlay"],
    ["expected_artifact_digest", digest],
  ]);
test("shared viewer renders actual geometry, links journal window and sends digest-bound ACK", async () => {
  const original = globalThis.fetch;
  const artifact = fixture(),
    digest = await researchDigest(artifact),
    calls = [];
  let n = 0;
  globalThis.fetch = async (path, init) => {
    calls.push([path, init]);
    return new Response(
      JSON.stringify(
        n++ === 0
          ? { job_id: "job1" }
          : n === 2
            ? { state: "complete", artifact, artifact_digest: digest }
            : { accepted: true },
      ),
    );
  };
  try {
    const page = root(),
      viewer = mountResearchViewer(page);
    assert.equal(
      await viewer.load(command(artifact, digest), "fixture-token"),
      true,
    );
    const canvas = page.querySelector("#research-canvas");
    assert.equal(canvas.dataset.renderAck, "accepted");
    assert.deepEqual(JSON.parse(calls[2][1].body), {
      artifact_digest: digest,
      rendered_fill_ids: ["f1"],
    });
    const row = page.querySelector("#research-fills").children[0];
    assert.equal(row.dataset.fillId, "f1");
    row.listeners.click();
    assert.deepEqual(JSON.parse(canvas.dataset.viewRange), [0, 60000]);
    assert.ok(row.children.some((c) => c.textContent === "10.000"));
  } finally {
    globalThis.fetch = original;
  }
});
test("selection invalidation while request is pending never renders or ACKs stale output", async () => {
  const original = globalThis.fetch;
  let resolve;
  const calls = [];
  globalThis.fetch = (path) => {
    calls.push(path);
    return new Promise((r) => (resolve = r));
  };
  try {
    const page = root(),
      viewer = mountResearchViewer(page),
      artifact = fixture();
    const pending = viewer.load(
      command(artifact, "b".repeat(64)),
      "fixture-token",
    );
    viewer.clear();
    resolve(new Response('{"job_id":"old"}'));
    assert.equal(await pending, false);
    assert.equal(calls.length, 1);
    assert.equal(
      page.querySelector("#research-canvas").dataset.renderAck,
      undefined,
    );
  } finally {
    globalThis.fetch = original;
  }
});
import { readFileSync } from "node:fs";
const producerFixture = JSON.parse(
  readFileSync(new URL("./fixtures/research-summary.json", import.meta.url)),
);
for (const valid of [true, false])
  test(`authenticated summary ${valid ? "displays exact history and risk lines" : "refusal leaves valid chart without risk lines"}`, async () => {
    const original = globalThis.fetch,
      artifact = producerFixture.chart,
      digest = producerFixture.envelope.summary.artifact_digest;
    const envelope = structuredClone(producerFixture.envelope);
    if (!valid) envelope.summary_digest = "0".repeat(64);
    const responses = [
      { job_id: "chart-job" },
      { state: "complete", artifact, artifact_digest: digest },
      { accepted: true },
      envelope,
    ];
    const paths = [];
    globalThis.fetch = async (path) => {
      paths.push(path);
      return new Response(JSON.stringify(responses.shift()));
    };
    try {
      const page = root(),
        viewer = mountResearchViewer(page);
      assert.equal(
        await viewer.load(command(artifact, digest), "fixture-token", {
          jobId: "execution-job",
          inputDigest: envelope.summary.input_digest,
        }),
        true,
      );
      const canvas = page.querySelector("#research-canvas");
      assert.equal(canvas.dataset.renderAck, "accepted");
      assert.equal(
        paths[3],
        "/api/control/research-executions/execution-job/summary",
      );
      if (valid) {
        assert.equal(canvas.dataset.summaryDigest, envelope.summary_digest);
        assert.equal(JSON.parse(canvas.dataset.renderedLevels).length, 2);
        assert.ok(page.querySelector("#research-summary").children.length > 0);
      } else {
        assert.equal(canvas.dataset.summaryDigest, undefined);
        assert.deepEqual(JSON.parse(canvas.dataset.renderedLevels), []);
        assert.match(
          page.querySelector("#research-summary").textContent,
          /DATA_UNAVAILABLE/,
        );
      }
    } finally {
      globalThis.fetch = original;
    }
  });
