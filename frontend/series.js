import { validateSeries } from "./model.js";
export function sourceText(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return [...new Set(value.map(sourceText).filter(Boolean))].join(" · ");
  if (value && typeof value === "object")
    return [value.source_kind, value.source_ref].filter(Boolean).join(": ");
  return "";
}
const rank = {
  AVAILABLE: 0,
  PARTIAL: 1,
  UNVERIFIED: 2,
  UNAVAILABLE: 3,
  DATA_UNAVAILABLE: 3,
};
function stateOf(v) {
  return typeof v === "string" ? v : v?.status;
}
export function quality(objects, hasRecords = true) {
  const states = objects
    .filter(Boolean)
    .map((o) => ({
      state: stateOf(o.availability),
      reason: o.availability?.reason || o.reason,
    }))
    .filter((o) => o.state);
  const invalid = states.some((o) => !Object.hasOwn(rank, o.state));
  const status = invalid
    ? "UNVERIFIED"
    : states.reduce(
        (worst, o) => (rank[o.state] > rank[worst] ? o.state : worst),
        states.length ? "AVAILABLE" : "PARTIAL",
      );
  const reasons = [
    ...new Set(
      states
        .map((o) => o.reason)
        .filter((v) => typeof v === "string" && v.trim()),
    ),
  ];
  const provenance = objects
    .filter(Boolean)
    .flatMap((o) =>
      Array.isArray(o.provenance)
        ? o.provenance
        : o.provenance
          ? [o.provenance]
          : [],
    );
  const unique = [
    ...new Map(provenance.map((p) => [JSON.stringify(p), p])).values(),
  ];
  const coverage = objects
    .filter(Boolean)
    .flatMap((o) =>
      o.coverage ? (Array.isArray(o.coverage) ? o.coverage : [o.coverage]) : [],
    );
  const source = [
    ...new Set(
      [
        sourceText(unique),
        ...objects.filter(Boolean).map((o) => sourceText(o.source)),
      ].filter(Boolean),
    ),
  ].join(" · ");
  const as_of =
    objects.find((o) => Number.isSafeInteger(o?.as_of))?.as_of ?? null;
  return {
    availability: hasRecords ? status : "DATA_UNAVAILABLE",
    reason:
      reasons.join(" · ") ||
      (invalid
        ? "Unsupported canonical availability"
        : !hasRecords
          ? "No records returned for requested range"
          : !states.length
            ? "Coverage/verification not supplied by provider"
            : status === "AVAILABLE"
              ? ""
              : `Provider reports ${status} without a reason`),
    provenance: unique,
    coverage,
    source,
    as_of,
    renderable: hasRecords && ["AVAILABLE", "PARTIAL"].includes(status),
  };
}
export function adaptSeries(payload, kind, q) {
  const data = payload.data,
    meta = Array.isArray(data) ? {} : data;
  let rows = Array.isArray(data) ? data : data?.rows;
  if (!meta || !Array.isArray(rows) || rows.length > 1000)
    throw Error("Corrupt series envelope");
  const evidence = quality([payload, meta, ...rows], rows.length > 0);
  const completeness =
    payload.coverage?.status ||
    meta.coverage?.status ||
    meta.completeness ||
    "UNKNOWN";
  const result = {
    ...meta,
    ...evidence,
    completeness,
    next_cursor: payload.next_cursor ?? null,
    rows: [],
    source_rows: rows,
    data_type:
      evidence.provenance.some((p) => p?.source_kind === "derived") ||
      rows.some((r) => r.kind === "derived")
        ? "derived"
        : evidence.provenance.some((p) =>
              ["bybit_rest", "bybit_archive"].includes(p?.source_kind),
            )
          ? "raw"
          : "origin unknown",
  };
  if (!evidence.renderable) return result;
  if (!evidence.source) throw Error("Corrupt series: missing provenance");
  if (kind === "footprint" && rows.some((r) => !r.levels)) {
    if (rows.some((r) => r.side_basis && r.side_basis !== "taker"))
      throw Error("Unsupported footprint side basis");
    if (rows.some((r) => r.price == null))
      return {
        ...result,
        availability: "DATA_UNAVAILABLE",
        renderable: false,
        reason: [
          result.reason,
          "Price-level footprint unavailable: canonical totals have no price levels",
        ]
          .filter(Boolean)
          .join(" · "),
      };
    const groups = new Map();
    for (const r of rows) {
      const ts = r.ts ?? r.timestamp_ms;
      let g = groups.get(ts);
      if (!g) {
        g = { ts, levels: [] };
        groups.set(ts, g);
      }
      g.levels.push({
        price: r.price,
        taker_buy: r.buy_volume,
        taker_sell: r.sell_volume,
      });
    }
    rows = [...groups.values()];
  } else
    rows = rows.map((r) => ({
      ...r,
      ts: r.ts ?? r.timestamp_ms,
      ...(kind === "volume"
        ? { value: r.volume }
        : kind === "funding"
          ? { value: r.value ?? r.rate }
          : {}),
    }));
  validateSeries(rows, kind, q.start_ms, q.end_ms);
  if (payload.next_cursor) {
    result.availability =
      result.availability === "AVAILABLE" ? "PARTIAL" : result.availability;
    result.reason = [
      result.reason,
      "Bounded page: more server data is available; use Next data page.",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  return { ...result, rows };
}
