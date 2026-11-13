// Pure, fail-closed capability classification. This module never probes an endpoint.
// API integration must supply verified capabilities with source/revision and authorization.
const SUPPORTED = new Set([
  "candles", "volume", "footprint", "delta", "cvd", "profile", "poc",
  "vwap", "open_interest", "funding", "mark", "index",
]);
const STATES = new Set(["AVAILABLE", "PARTIAL", "UNAVAILABLE", "UNVERIFIED"]);
const ROUTES = Object.freeze({
  candles: "/api/candles", volume: "/api/candles",
  footprint: "/api/footprint", open_interest: "/api/open-interest",
  funding: "/api/funding", mark: "/api/candles", index: "/api/candles",
});

const REASON = /^[a-z][a-z0-9_]{0,79}$/;

export function classifyPanelCapability(panel, record) {
  if (!SUPPORTED.has(panel)) throw new TypeError("Unknown panel");
  const unavailable = (reason) => ({
    panel, requestAllowed: false, state: "UNAVAILABLE", reason,
  });
  if (record == null) return unavailable("capability_not_advertised");
  if (typeof record !== "object" || Array.isArray(record))
    return unavailable("invalid_capability");
  if (record.panel !== panel || !STATES.has(record.status))
    return unavailable("invalid_capability");
  if (typeof record.reason !== "string" || !REASON.test(record.reason))
    return unavailable("invalid_capability");
  if (record.status === "UNAVAILABLE" || record.status === "UNVERIFIED")
    return { panel, requestAllowed: false, state: record.status, reason: record.reason };
  // An AVAILABLE status alone is insufficient. Bind it to a verified API
  // resource and an immutable data revision, not the number of chart rows.
  if (
    typeof record.resource !== "string" ||
    !/^\/[a-zA-Z0-9_./-]{1,160}$/.test(record.resource) ||
    record.resource.startsWith("//") ||
    typeof record.revision !== "string" ||
    !/^[a-zA-Z0-9_.:-]{1,128}$/.test(record.revision) ||
    typeof record.source_ref !== "string" ||
    !/^[a-zA-Z0-9_.:/-]{1,256}$/.test(record.source_ref)
  ) return unavailable("unverified_capability_binding");
  if (record.resource !== ROUTES[panel])
    return unavailable("unsupported_panel_resource");
  return {
    panel, requestAllowed: true, state: record.status,
    reason: record.reason, resource: record.resource,
    revision: record.revision, source_ref: record.source_ref,
  };
}

export function panelsToRequest(capabilities, panels) {
  if (!Array.isArray(panels)) throw new TypeError("Panels must be an array");
  const seen = new Set();
  return panels.filter((panel) => {
    if (seen.has(panel)) return false;
    seen.add(panel);
    return classifyPanelCapability(panel, capabilities?.[panel]).requestAllowed;
  });
}
