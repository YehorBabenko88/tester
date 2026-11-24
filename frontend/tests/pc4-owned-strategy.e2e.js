// ROOT ONLY: private operator capability, real owned service, NO routes or server.
// Never attach the case file, token, request headers, trace or raw browser errors.
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { researchDigest } from "../research-chart.js";
if (process.env.TESTER_PC4_OPT_IN !== "pc4-owned")
  throw Error("pc4-owned opt-in required");
const raw = readFileSync(process.env.TESTER_PC4_STRATEGY_CASE_FILE);
if (raw.length > 65536) throw Error("Private case exceeds bounded bytes");
let privateCase;
try {
  privateCase = JSON.parse(raw.toString("utf8").replace(/^\uFEFF/, ""));
} catch {
  throw Error("Invalid private strategy case JSON (redacted)");
}
const { token, ...c } = privateCase;
const hash = (value) =>
  typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
if (
  c.source_sha !== process.env.TESTER_PC4_SOURCE_SHA ||
  !/^[a-f0-9]{40}$/.test(c.source_sha || "") ||
  !/^[a-f0-9]{40}$/.test(process.env.TESTER_PC4_HARNESS_SHA || "") ||
  !hash(c.input_digest) ||
  typeof token !== "string" ||
  !token.trim() ||
  token.length > 8192 ||
  typeof c.actor !== "string" ||
  !/^[A-Za-z0-9:._-]{1,100}$/.test(c.actor) ||
  c.symbol !== "BTCUSDT" ||
  !["linear", "inverse"].includes(c.category) ||
  ![
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
  ].includes(c.interval) ||
  ![c.start_ms, c.end_ms].every(
    (t) => Number.isSafeInteger(t) && t >= 0 && t % 60000 === 0,
  ) ||
  c.start_ms >= c.end_ms ||
  c.end_ms - c.start_ms > 366 * 86400000
)
  throw Error(
    "Invalid private strategy case: exact pin, admitted terminal identity, bounded UTC range and scoped capability required",
  );
const iso = (t) => new Date(t).toISOString().slice(0, 16);
async function secretFill(locator, value) {
  try {
    await locator.evaluate((node, secret) => {
      node.value = secret;
      node.dispatchEvent(new Event("input", { bubbles: true }));
    }, value);
  } catch {
    throw Error("Capability entry failed (redacted)");
  }
}
async function passwordCleared(page) {
  expect(
    await page.locator("#strategy-capability").evaluate((n) => n.value === ""),
    "Capability field must be empty before evidence",
  ).toBe(true);
}
const executionResponse = (page) =>
  page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/api/control/research-executions" &&
      r.request().method() === "POST",
  );
test("PC4 actual registered F execution to digest-bound summary, SLTP canvas and trade journal", async ({
  page,
}, testInfo) => {
  const report = {
    classification: "ROOT_ONLY_AUTHENTICATED_NO_ROUTE_MOCK",
    application_source_sha: c.source_sha,
    harness_source_sha: process.env.TESTER_PC4_HARNESS_SHA,
    source_identity:
      "operator supplied release pins; process/PG provenance verified separately by root",
    actor: c.actor,
    input_digest: c.input_digest,
    selection: {
      symbol: c.symbol,
      category: c.category,
      interval: c.interval,
      start_ms: c.start_ms,
      end_ms: c.end_ms,
    },
    page_errors: [],
    console_errors: [],
    api: [],
    negative_checks: [
      {
        case: "wrong_category",
        status: "NOT_RUN",
        reason:
          "Root must separately test a mismatched approved fixture; this harness enforces the accepted artifact category equals actual selected case category.",
      },
    ],
    coverage_gate: {
      real_or_UNKNOWN_registration:
        "NOT_RUN: private case contains only an admitted synthetic pair; root must independently prove registration rejection and unchanged F/job ledger",
    },
    started_utc: new Date().toISOString(),
  };
  let assertionsCompleted = false;
  const accepted = [],
    chartResults = [],
    pending = [];
  const digestError = (s) => createHash("sha256").update(s).digest("hex");
  page.on("pageerror", (e) =>
    report.page_errors.push({ message_sha256: digestError(e.message) }),
  );
  page.on("console", (m) => {
    if (m.type() === "error")
      report.console_errors.push({ message_sha256: digestError(m.text()) });
  });
  page.on("response", (response) => {
    const u = new URL(response.url());
    if (
      u.origin !== "http://127.0.0.1:18767" ||
      !u.pathname.startsWith("/api/")
    )
      return;
    report.api.push({
      path: u.pathname,
      status: response.status(),
      method: response.request().method(),
    });
    if (response.ok() && u.pathname.startsWith("/api/control/research/"))
      pending.push(
        (async () => {
          try {
            const result = await response.json();
            if (result.state === "complete" && result.artifact)
              chartResults.push(result);
          } catch {}
        })(),
      );
  });
  try {
    await page.goto("/");
    await page.locator("#category").selectOption(c.category);
    await page.getByLabel("Search symbols").fill(c.symbol);
    await expect(
      page.locator(`[data-row="${c.category}:${c.symbol}"]`),
    ).toBeVisible();
    await page.locator("#download-options").evaluate((n) => (n.open = true));
    await page.locator("#start").fill(iso(c.start_ms));
    await page.locator("#end").fill(iso(c.end_ms));
    await page.locator("#interval").selectOption(c.interval);
    await page
      .getByRole("button", { name: `Chart ${c.symbol}`, exact: true })
      .click();
    await page.locator("#strategy-bind").click();
    expect(
      (await page.locator("#strategy-binding").textContent()).includes(
        c.symbol,
      ),
    ).toBe(true);
    await page.locator("#strategy-input-digest").fill(c.input_digest);
    await secretFill(
      page.locator("#strategy-capability"),
      "pc4-intentionally-invalid-capability",
    );
    let response = executionResponse(page);
    await page.locator("#strategy-run").click();
    const denied = await response;
    expect(denied.status()).toBe(401);
    await passwordCleared(page);
    await expect
      .poll(async () =>
        (
          await page.locator("#strategy-execution-status").textContent()
        ).includes("HTTP_401"),
      )
      .toBe(true);
    report.negative_checks.push({
      case: "wrong_token",
      status: 401,
      accepted_execution: false,
    });
    // Unknown pair has no accepted execution; this is not a real-coverage admission proof.
    await page.locator("#strategy-input-digest").fill("0".repeat(64));
    await secretFill(page.locator("#strategy-capability"), token);
    response = executionResponse(page);
    await page.locator("#strategy-run").click();
    const unknown = await response;
    expect(unknown.status()).toBe(403);
    await passwordCleared(page);
    await expect
      .poll(async () =>
        (
          await page.locator("#strategy-execution-status").textContent()
        ).includes("HTTP_403"),
      )
      .toBe(true);
    report.negative_checks.push({
      case: "unregistered_or_ungranted_pair",
      status: 403,
      accepted_execution: false,
      scope: "not real/UNKNOWN coverage proof",
    });
    await page.locator("#strategy-input-digest").fill(c.input_digest);
    await secretFill(page.locator("#strategy-capability"), token);
    const summaryResponse = page.waitForResponse((r) =>
      /^\/api\/control\/research-executions\/[^/]+\/summary$/.test(
        new URL(r.url()).pathname,
      ),
    );
    response = executionResponse(page);
    await page.locator("#strategy-run").click();
    const submitted = await response;
    expect(submitted.status()).toBe(202);
    const job = await submitted.json();
    expect(typeof job.job_id).toBe("string");
    accepted.push(job.job_id);
    report.execution_job_id = job.job_id;
    await passwordCleared(page);
    const summaryHttp = await summaryResponse;
    expect(summaryHttp.status()).toBe(200);
    expect(new URL(summaryHttp.url()).pathname).toBe(
      `/api/control/research-executions/${encodeURIComponent(job.job_id)}/summary`,
    );
    const envelope = await summaryHttp.json();
    const canvas = page.locator("#research-canvas");
    await expect
      .poll(async () => hash(await canvas.getAttribute("data-summary-digest")))
      .toBe(true);
    await expect(canvas).toHaveAttribute("data-render-ack", "accepted");
    expect(await researchDigest(envelope.summary)).toBe(
      envelope.summary_digest,
    );
    expect(envelope.summary.input_digest).toBe(c.input_digest);
    expect(envelope.summary.certification).toEqual({
      scope: "SYNTHETIC_ONLY",
      real_market_coverage: "NOT_PROVEN",
    });
    expect(await canvas.getAttribute("data-summary-digest")).toBe(
      envelope.summary_digest,
    );
    expect(await canvas.getAttribute("data-artifact-digest")).toBe(
      envelope.summary.artifact_digest,
    );
    await Promise.allSettled(pending);
    const result = chartResults.find(
      (r) => r.artifact_digest === envelope.summary.artifact_digest,
    );
    expect(Boolean(result)).toBe(true);
    const artifact = result.artifact;
    expect(artifact.synthetic).toBe(true);
    expect(artifact.execution_mode).toBe("SIMULATED");
    for (const key of ["symbol", "category", "interval", "start_ms", "end_ms"])
      expect(artifact[key]).toBe(c[key]);
    expect(artifact.run_id).toBe(envelope.summary.run_id);
    expect(envelope.summary.trades.length).toBeGreaterThan(0);
    expect(envelope.summary.levels.length).toBeGreaterThan(0);
    const rendered = JSON.parse(
      await canvas.getAttribute("data-rendered-levels"),
    );
    expect(rendered.length).toBe(envelope.summary.levels.length);
    for (const level of envelope.summary.levels) {
      const entry = artifact.markers.find(
        (m) => m.fill_id === level.entry_fill_id,
      );
      expect(Boolean(entry)).toBe(true);
      expect([level.start_ms, level.end_ms, level.known_at_ms]).toEqual([
        entry.window_start_ms,
        entry.window_end_ms,
        entry.known_at_ms,
      ]);
      expect(
        envelope.summary.trades.some(
          (t) =>
            t.entry_fill_id === level.entry_fill_id &&
            t[level.kind === "sl" ? "stop_loss" : "take_profit"] ===
              level.price,
        ),
      ).toBe(true);
      expect(
        rendered.some(
          (r) =>
            r.entry_fill_id === level.entry_fill_id &&
            r.kind === level.kind &&
            r.price === level.price,
        ),
      ).toBe(true);
    }
    const text = await page.locator("#research-summary").textContent();
    expect(text.includes("no IID confidence claim")).toBe(true);
    expect(text.includes("entry uncertainty window")).toBe(true);
    for (const value of Object.values(envelope.summary.metrics)) {
      if (typeof value === "string") expect(text.includes(value)).toBe(true);
    }
    const trade = envelope.summary.trades[0],
      entry = artifact.markers.find((m) => m.fill_id === trade.entry_fill_id);
    await page
      .getByRole("row", {
        name: `Inspect simulated trade ${trade.id}`,
        exact: true,
      })
      .click();
    expect(JSON.parse(await canvas.getAttribute("data-view-range"))).toEqual([
      entry.window_start_ms,
      entry.window_end_ms,
    ]);
    await page.locator("#research-reset").click();
    expect(JSON.parse(await canvas.getAttribute("data-view-range"))).toEqual([
      artifact.start_ms,
      artifact.end_ms,
    ]);
    expect(
      JSON.parse(await canvas.getAttribute("data-rendered-levels")),
    ).toHaveLength(envelope.summary.levels.length);
    await passwordCleared(page);
    await page.locator(".strategy-execution").screenshot({
      path: testInfo.outputPath("authenticated-synthetic-summary.png"),
    });
    await testInfo.attach("authenticated-synthetic-summary", {
      path: testInfo.outputPath("authenticated-synthetic-summary.png"),
      contentType: "image/png",
    });
    report.artifact_digest = envelope.summary.artifact_digest;
    report.summary_digest = envelope.summary_digest;
    report.run_id = envelope.summary.run_id;
    report.metrics = envelope.summary.metrics;
    report.trades = envelope.summary.trades;
    report.equity_curve = envelope.summary.equity_curve;
    report.brackets = envelope.summary.brackets;
    report.levels = envelope.summary.levels;
    report.rendered_levels = rendered;
    report.accepted_execution_jobs = accepted;
    report.source_sha = envelope.summary.source_sha;
    expect(report.page_errors).toEqual([]);
    assertionsCompleted = true;
  } finally {
    report.assertions_completed = assertionsCompleted;
    report.finished_utc = new Date().toISOString();
    await testInfo.attach("pc4-strategy-sanitized-evidence", {
      body: JSON.stringify(report, null, 2),
      contentType: "application/json",
    });
  }
});
