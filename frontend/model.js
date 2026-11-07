const DECIMAL = /^-?\d+(?:\.\d+)?$(?![\s\S])/;
export function isDecimal(v) {
  return typeof v === "string" && v.length <= 100 && DECIMAL.test(v);
}
export function compareDecimal(a, b, direction = 1) {
  if (!isDecimal(a)) return isDecimal(b) ? 1 : 0;
  if (!isDecimal(b)) return -1;
  const parts = (v) => {
    let neg = v.startsWith("-");
    let [whole, frac = ""] = v.replace(/^-/, "").split(".");
    return { neg, whole: whole.replace(/^0+(?=\d)/, ""), frac };
  };
  const x = parts(a),
    y = parts(b),
    scale = Math.max(x.frac.length, y.frac.length);
  const n = BigInt((x.neg ? "-" : "") + x.whole + x.frac.padEnd(scale, "0"));
  const m = BigInt((y.neg ? "-" : "") + y.whole + y.frac.padEnd(scale, "0"));
  return (n < m ? -1 : n > m ? 1 : 0) * direction;
}
export function metric(m) {
  const status =
    typeof m?.availability === "object"
      ? m.availability.status
      : m?.availability;
  const reason = m?.availability?.reason || m?.reason;
  const refs = m?.provenance;
  const source =
    m?.source ||
    (Array.isArray(refs)
      ? refs
          .map((p) => p.source_ref)
          .filter(Boolean)
          .join(" · ")
      : null);
  const as_of = m?.as_of ?? m?.timestamp_ms;
  if (
    !m ||
    !isDecimal(m.value) ||
    !source ||
    !Number.isSafeInteger(as_of) ||
    !["AVAILABLE", "PARTIAL"].includes(status)
  )
    return {
      ...m,
      source,
      as_of,
      value: null,
      availability: ["UNAVAILABLE", "UNVERIFIED"].includes(status)
        ? status
        : "DATA_UNAVAILABLE",
      availability_detail: m?.availability,
      reason: reason || "Value, provenance or observation time not supplied",
    };
  return { ...m, source, as_of, availability: status, reason };
}
export function progress(job) {
  const p = job.progress || {},
    done = Number(p.completed),
    total = Number(p.total);
  const known =
    p.total != null &&
    Number.isFinite(total) &&
    total > 0 &&
    Number.isFinite(done) &&
    done >= 0;
  return {
    percent: known
      ? Math.min(
          job.state === "COMPLETE" && p.verified === true ? 100 : 99,
          Math.max(0, Math.floor((done / total) * 100)),
        )
      : null,
  };
}
export function validateSeries(rows, kind, start, end) {
  if (!Array.isArray(rows) || rows.length > 1000)
    throw Error("Corrupt or oversized series");
  let prev = -Infinity;
  for (const row of rows) {
    if (
      !Number.isSafeInteger(row.ts) ||
      row.ts < start ||
      row.ts >= end ||
      (kind === "profile" ? row.ts < prev : row.ts <= prev)
    )
      throw Error("Corrupt timestamp order or range");
    prev = row.ts;
    const fields =
      kind === "candles"
        ? ["open", "high", "low", "close"]
        : kind === "footprint"
          ? []
          : kind === "profile"
            ? ["price", "volume"]
            : ["value"];
    for (const key of fields)
      if (!isDecimal(row[key])) throw Error("Corrupt decimal " + key);
    if (
      kind === "candles" &&
      (compareDecimal(row.low, row.high) > 0 ||
        compareDecimal(row.open, row.high) > 0 ||
        compareDecimal(row.close, row.high) > 0 ||
        compareDecimal(row.open, row.low) < 0 ||
        compareDecimal(row.close, row.low) < 0)
    )
      throw Error("Corrupt OHLC bounds");
    if (kind === "footprint") {
      if (!Array.isArray(row.levels) || row.levels.length > 500)
        throw Error("Corrupt footprint levels");
      for (const level of row.levels)
        if (
          !["price", "taker_buy", "taker_sell"].every((k) =>
            isDecimal(level[k]),
          )
        )
          throw Error("Corrupt footprint decimal");
    }
  }
  return rows;
}
export const PANELS = [
  ["candles", "OHLCV Candlesticks", "raw"],
  ["volume", "Volume", "raw"],
  ["footprint", "Footprint · Taker Buy / Sell", "derived"],
  ["delta", "Delta", "derived"],
  ["cvd", "Cumulative Volume Delta", "derived"],
  ["profile", "Volume Profile", "derived"],
  ["poc", "Point of Control (POC)", "derived"],
  ["vwap", "VWAP", "derived"],
  ["open_interest", "Open Interest", "raw"],
  ["funding", "Funding Rate", "raw"],
  ["mark", "Mark Price", "raw"],
  ["index", "Index Price", "raw"],
];
