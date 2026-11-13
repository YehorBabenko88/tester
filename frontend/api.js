export async function request(path, params = {}, options = {}) {
  const timeout = AbortSignal.timeout(20000);
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeout])
    : timeout;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    for (const item of Array.isArray(value) ? value : [value])
      if (item != null) query.append(key, item);
  }
  const response = await (options.fetcher || fetch)(
    path + (query.size ? "?" + query : ""),
    {
      signal,
      method: options.method || "GET",
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      },
      ...(options.body ? { body: JSON.stringify(options.body) } : {}),
    },
  );
  const declared = Number(response.headers.get("content-length"));
  if (declared > 8_000_000) throw Error("Response exceeds 8 MB safety limit");
  const reader = response.body?.getReader();
  let raw = "";
  if (reader) {
    let size = 0;
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 8_000_000) {
        await reader.cancel();
        throw Error("Response exceeds 8 MB safety limit");
      }
      raw += decoder.decode(value, { stream: true });
    }
    raw += decoder.decode();
  } else raw = await response.text();
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw Error("Invalid JSON response");
  }
  if (!response.ok) {
    const e = payload.error || payload.detail || {};
    const failure = Error(
      `${response.status} ${e.code || "API_ERROR"}: ${e.message || String(e)}`,
    );
    failure.status = response.status;
    failure.code = e.code || "API_ERROR";
    throw failure;
  }
  const schemaVersion = options.schemaVersion || "1.0";
  if (payload.schema_version !== schemaVersion || !Object.hasOwn(payload, "data"))
    throw Error("Unsupported API schema");
  return payload;
}
