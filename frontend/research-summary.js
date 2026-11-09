import { researchDigest, validateResearchChart } from "./research-chart.js";
const SOURCE_SHA = "998c5bc89d1b35c9ad3047b1e50dcb1295120e31";
const fail = () => {
  throw Error("UNQUALIFIED_RESEARCH_SUMMARY");
};
const keys = (v, names) => {
  if (
    !v ||
    typeof v !== "object" ||
    Array.isArray(v) ||
    Object.keys(v).length !== names.length ||
    !names.every((k) => Object.hasOwn(v, k))
  )
    fail();
};
const hash = (v) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const id = (v) =>
  typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$/.test(v);
const time = (v) => Number.isSafeInteger(v) && v >= 0;
const decimal = (v) =>
  typeof v === "string" &&
  v.length <= 160 &&
  /^-?[0-9]+(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?$/.test(v) &&
  Number.isFinite(Number(v));
const positive = (v) => decimal(v) && Number(v) > 0;
const array = (v, max) => {
  if (!Array.isArray(v) || v.length > max) fail();
};
export async function validateResearchSummary(
  envelope,
  { artifact, artifactDigest, inputDigest },
) {
  validateResearchChart(artifact);
  keys(envelope, ["summary", "summary_digest"]);
  const s = envelope.summary;
  keys(s, [
    "schema_version",
    "input_digest",
    "artifact_digest",
    "run_id",
    "source_sha",
    "engine_version",
    "certification",
    "metrics",
    "trades",
    "equity_curve",
    "brackets",
    "levels",
  ]);
  if (
    s.schema_version !== "tester.research-summary/1" ||
    !hash(inputDigest) ||
    s.input_digest !== inputDigest ||
    !hash(artifactDigest) ||
    s.artifact_digest !== artifactDigest ||
    s.run_id !== artifact.run_id ||
    s.source_sha !== SOURCE_SHA ||
    artifact.revision !== SOURCE_SHA ||
    s.engine_version !== "strategy-bar-1.1.0" ||
    !hash(envelope.summary_digest)
  )
    fail();
  keys(s.certification, ["scope", "real_market_coverage"]);
  if (
    s.certification.scope !== "SYNTHETIC_ONLY" ||
    s.certification.real_market_coverage !== "NOT_PROVEN"
  )
    fail();
  if (
    (await researchDigest(artifact)) !== artifactDigest ||
    (await researchDigest(s)) !== envelope.summary_digest
  )
    fail();
  const metricNames = [
    "expectancy",
    "exposure",
    "fees",
    "funding_costs",
    "max_drawdown",
    "net_realized_pnl",
    "profit_factor",
    "win_rate",
  ];
  keys(s.metrics, [...metricNames, "trade_count", "confidence"]);
  for (const k of metricNames)
    if (
      !(
        s.metrics[k] === null &&
        ["expectancy", "profit_factor", "win_rate"].includes(k)
      ) &&
      !decimal(s.metrics[k])
    )
      fail();
  if (!Number.isSafeInteger(s.metrics.trade_count) || s.metrics.trade_count < 0)
    fail();
  keys(s.metrics.confidence, ["method", "reason"]);
  if (
    s.metrics.confidence.method !== "not_estimated" ||
    s.metrics.confidence.reason !==
      "dependent, selected historical trades; no IID confidence claim"
  )
    fail();
  array(s.trades, 500);
  array(s.equity_curve, 2001);
  array(s.brackets, 2000);
  array(s.levels, 1000);
  if (s.metrics.trade_count !== s.trades.length || !s.equity_curve.length)
    fail();
  const markers = new Map(artifact.markers.map((m) => [m.fill_id, m]));
  const tradeIds = new Set(),
    exits = new Set();
  for (const t of s.trades) {
    keys(t, [
      "id",
      "side",
      "quantity",
      "entry_fill_id",
      "exit_fill_id",
      "entry_timestamp_ms",
      "exit_timestamp_ms",
      "entry_price",
      "exit_price",
      "stop_loss",
      "take_profit",
      "gross_pnl",
      "net_pnl",
      "fees",
      "funding_costs",
      "reason",
    ]);
    const entry = markers.get(t.entry_fill_id),
      exit = markers.get(t.exit_fill_id);
    if (
      !id(t.id) ||
      tradeIds.has(t.id) ||
      exits.has(t.exit_fill_id) ||
      !["long", "short"].includes(t.side) ||
      !positive(t.quantity) ||
      !["entry_price", "exit_price", "stop_loss", "take_profit"].every((k) =>
        positive(t[k]),
      ) ||
      !["gross_pnl", "net_pnl", "fees", "funding_costs"].every((k) =>
        decimal(t[k]),
      ) ||
      ![
        "stop_loss",
        "take_profit",
        "trailing_stop",
        "liquidation",
        "exit_condition",
      ].includes(t.reason) ||
      !entry ||
      !exit ||
      entry.role !== "entry" ||
      exit.role !== "exit" ||
      exit.closed_trade_id !== t.id ||
      entry.position_id !== exit.position_id ||
      t.entry_timestamp_ms !== entry.timestamp_ms ||
      t.exit_timestamp_ms !== exit.timestamp_ms ||
      t.entry_price !== entry.price ||
      t.exit_price !== exit.price ||
      entry.timestamp_ms > exit.timestamp_ms ||
      entry.side !== (t.side === "long" ? "buy" : "sell")
    )
      fail();
    tradeIds.add(t.id);
    exits.add(t.exit_fill_id);
  }
  let previous = -1;
  for (const p of s.equity_curve) {
    keys(p, ["timestamp_ms", "cash", "equity", "margin"]);
    if (
      !time(p.timestamp_ms) ||
      p.timestamp_ms <= previous ||
      p.timestamp_ms < artifact.start_ms ||
      p.timestamp_ms > artifact.end_ms ||
      !["cash", "equity", "margin"].every((k) => decimal(p[k]))
    )
      fail();
    previous = p.timestamp_ms;
  }
  if (
    s.equity_curve[0].timestamp_ms !== artifact.start_ms ||
    previous !== artifact.end_ms
  )
    fail();
  for (const b of s.brackets) {
    keys(b, [
      "timestamp_ms",
      "entry_fill_id",
      "stop_loss",
      "take_profit",
      "trailing_stop",
    ]);
    const entry = markers.get(b.entry_fill_id);
    if (
      !time(b.timestamp_ms) ||
      b.timestamp_ms < artifact.start_ms ||
      b.timestamp_ms > artifact.cutoff_ms ||
      !entry ||
      entry.role !== "entry" ||
      b.timestamp_ms < entry.known_at_ms ||
      !positive(b.stop_loss) ||
      !positive(b.take_profit) ||
      (b.trailing_stop !== null && !positive(b.trailing_stop))
    )
      fail();
  }
  const seen = new Set();
  for (const l of s.levels) {
    keys(l, [
      "entry_fill_id",
      "kind",
      "price",
      "start_ms",
      "end_ms",
      "known_at_ms",
    ]);
    const entry = markers.get(l.entry_fill_id),
      key = JSON.stringify(l);
    if (
      seen.has(key) ||
      !["sl", "tp"].includes(l.kind) ||
      !entry ||
      entry.role !== "entry" ||
      !positive(l.price) ||
      l.start_ms !== entry.window_start_ms ||
      l.end_ms !== entry.window_end_ms ||
      l.known_at_ms !== entry.known_at_ms ||
      l.known_at_ms > artifact.cutoff_ms ||
      !s.trades.some(
        (t) =>
          t.entry_fill_id === l.entry_fill_id &&
          t[l.kind === "sl" ? "stop_loss" : "take_profit"] === l.price,
      )
    )
      fail();
    seen.add(key);
  }
  for (const trade of s.trades)
    for (const kind of ["sl", "tp"])
      if (
        !s.levels.some(
          (l) =>
            l.entry_fill_id === trade.entry_fill_id &&
            l.kind === kind &&
            l.price === trade[kind === "sl" ? "stop_loss" : "take_profit"],
        )
      )
        fail();
  return s;
}
export async function readResearchSummary({
  jobId,
  token,
  fetcher = fetch,
  ...binding
}) {
  if (
    !id(jobId) ||
    typeof token !== "string" ||
    !token.trim() ||
    token.length > 8192 ||
    !hash(binding.inputDigest)
  )
    throw Error("SUMMARY_AUTHORIZATION_REQUIRED");
  let response;
  try {
    response = await fetcher(
      `/api/control/research-executions/${encodeURIComponent(jobId)}/summary`,
      {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
        credentials: "omit",
        cache: "no-store",
        signal: AbortSignal.timeout(20000),
      },
    );
  } catch {
    throw Error("SUMMARY_TRANSPORT_UNAVAILABLE");
  }
  if (!response.ok) throw Error(`SUMMARY_HTTP_${response.status}`);
  if (Number(response.headers.get("content-length")) > 8000000)
    throw Error("SUMMARY_TOO_LARGE");
  const reader = response.body?.getReader();
  let raw = "";
  if (reader) {
    let bytes = 0;
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.length;
      if (bytes > 8000000) {
        await reader.cancel();
        throw Error("SUMMARY_TOO_LARGE");
      }
      raw += decoder.decode(value, { stream: true });
    }
    raw += decoder.decode();
  } else {
    raw = await response.text();
    if (raw.length > 8000000) throw Error("SUMMARY_TOO_LARGE");
  }
  let envelope;
  try {
    envelope = JSON.parse(raw);
  } catch {
    throw Error("INVALID_SUMMARY_JSON");
  }
  return validateResearchSummary(envelope, binding);
}
