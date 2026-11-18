// Fail-closed UI capability classification.
// Missing or unverified evidence must never trigger a historical-data request.
export function classifyPanelCapability(kind, advertised) {
  const unavailable = (state, reason) => ({
    state, reason, requestAllowed: false, resource: null,
    schema_version: null, candle_kind: null,
  });
  if (!advertised || typeof advertised !== "object" || Array.isArray(advertised))
    return unavailable("UNVERIFIED", "capability_not_advertised");
  const state = advertised.state;
  const reason = typeof advertised.reason === "string" && advertised.reason
    ? advertised.reason : "source_not_verified";
  if (state !== "AVAILABLE" && state !== "PARTIAL")
    return unavailable(typeof state === "string" ? state : "UNVERIFIED", reason);
  // Explicit, validated route metadata is required. Do not infer a route from
  // panel name or promote a NOT_BOUND_TO_UI_ROUTE source into an API route.
  const resource = advertised.resource;
  const schema = advertised.schema_version;
  if (typeof resource !== "string" ||
      !/^\/api\/[a-zA-Z0-9_/-]+$/.test(resource) ||
      resource.includes("..") ||
      (schema !== "1.0" && schema !== "1.1"))
    return unavailable("UNVERIFIED", "validated_route_required");
  return {
    state, reason, requestAllowed: true, resource,
    schema_version: schema,
    candle_kind: typeof advertised.candle_kind === "string"
      ? advertised.candle_kind : null,
  };
}
