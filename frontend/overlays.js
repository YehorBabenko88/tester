import { isDecimal } from "./model.js";
// Fixture-only proposal: no API, producer adapter or permission authority.
const keys = [
  "run_id",
  "snapshot_id",
  "revision",
  "symbol",
  "category",
  "interval",
  "start_ms",
  "end_ms",
  "cutoff_ms",
];
const text = (v) => typeof v === "string" && v.length > 0 && v.length <= 256;
const time = (v) => Number.isSafeInteger(v) && v >= 0;
export function presentOverlay(page, expected) {
  const reject = (reason) => ({
    integration_status: "NOT_INTEGRATED",
    availability: "DATA_UNAVAILABLE",
    evidence_mode: "SYNTHETIC_FIXTURE",
    coverage: "UNKNOWN",
    reason,
    events: [],
  });
  if (
    page?.schema_version !== "overlay-presentation-fixture/1" ||
    page.evidence_mode !== "SYNTHETIC_FIXTURE"
  )
    return reject("No qualified overlay integration; fixture contract only.");
  if (
    !expected ||
    !keys.every((k) => page.scope?.[k] === expected[k]) ||
    !keys.slice(0, 6).every((k) => text(expected[k])) ||
    !keys.slice(6).every((k) => time(expected[k])) ||
    expected.start_ms >= expected.end_ms
  )
    return reject("Overlay scope mismatch.");
  const s = page.source;
  if (
    !s ||
    s.permission !== "GRANTED" ||
    !["AVAILABLE", "PARTIAL"].includes(s.availability) ||
    !["OWNER_AUTHORIZED_EXPORT", "PUBLIC_ACCOUNT_DISCLOSURE"].includes(
      s.category,
    ) ||
    !["id", "revision", "evidence_ref"].every((k) => text(s[k]))
  )
    return reject(
      "Source permission, availability or evidence is unqualified.",
    );
  if (
    !["UNKNOWN", "PARTIAL", "VERIFIED"].includes(page.coverage) ||
    !Array.isArray(page.events) ||
    page.events.length > 1000
  )
    return reject("Invalid coverage or oversized page.");
  const seen = new Set();
  for (const e of page.events) {
    const w = e?.time_window;
    if (
      !e ||
      !text(e.event_id) ||
      seen.has(e.event_id) ||
      !["DISCLOSED", "INFERRED"].includes(e.classification) ||
      (e.classification === "INFERRED" && !text(e.hypothesis_id)) ||
      !text(e.evidence_ref) ||
      !isDecimal(e.price) ||
      !time(e.timestamp_ms) ||
      !time(e.known_at_ms) ||
      e.timestamp_ms < expected.start_ms ||
      e.timestamp_ms >= expected.end_ms ||
      e.known_at_ms < e.timestamp_ms ||
      e.known_at_ms > expected.cutoff_ms ||
      !["exact", "bar", "modeled_open"].includes(e.time_precision) ||
      !w ||
      !time(w.start_ms) ||
      !time(w.end_ms) ||
      w.start_ms > e.timestamp_ms ||
      w.end_ms < e.timestamp_ms ||
      (e.time_precision !== "exact" && w.start_ms === w.end_ms)
    )
      return reject(
        "Invalid, duplicate, future-known or uncertain event metadata.",
      );
    seen.add(e.event_id);
  }
  return {
    integration_status: "NOT_INTEGRATED",
    availability: s.availability,
    evidence_mode: "SYNTHETIC_FIXTURE",
    coverage: page.coverage,
    reason:
      "Synthetic fixture only; permission is a test value, not verified authorization.",
    scope: { ...page.scope },
    source: { ...s },
    events: page.events.map((e) => ({
      ...e,
      time_window: { ...e.time_window },
      label: `${e.classification === "INFERRED" ? "INFERRED hypothesis" : "DISCLOSED source event"} | ${e.price} | ${e.time_precision}: ${e.time_window.start_ms}-${e.time_window.end_ms} | known at ${e.known_at_ms} | fees ${e.fees ?? "UNKNOWN"} | SYNTHETIC FIXTURE`,
    })),
  };
}
