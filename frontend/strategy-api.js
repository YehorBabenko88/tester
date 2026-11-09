const intervals = [
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
];
const digest = (v) => typeof v === "string" && /^[0-9a-f]{64}$/.test(v);
const identifier = (v) =>
  typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$/.test(v);
const uuid = (v) =>
  typeof v === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const time = (v) => Number.isSafeInteger(v) && v >= 0;
export function validateExecutionSelection(value) {
  if (
    !value ||
    !["spot", "linear", "inverse"].includes(value.category) ||
    typeof value.symbol !== "string" ||
    !/^[A-Z0-9_-]{1,40}$/.test(value.symbol) ||
    !intervals.includes(value.interval) ||
    !time(value.start_ms) ||
    !time(value.end_ms) ||
    value.start_ms >= value.end_ms ||
    value.end_ms - value.start_ms > 366 * 86400000
  )
    throw Error("INVALID_SELECTION");
  return Object.fromEntries(
    ["category", "symbol", "interval", "start_ms", "end_ms"].map((k) => [
      k,
      value[k],
    ]),
  );
}
export function validateExecutionCommand(result, selection, jobId) {
  const command = result?.chart_command;
  if (
    result?.job_id !== jobId ||
    result?.state !== "complete" ||
    result.certification?.scope !== "SYNTHETIC_ONLY" ||
    result.certification?.real_market_coverage !== "NOT_PROVEN" ||
    command?.schema_version !== "tester.research-command/1" ||
    command.action !== "chart.overlay" ||
    command.venue !== "bybit" ||
    !uuid(command.request_id) ||
    !digest(command.expected_artifact_digest) ||
    !identifier(command.run_id) ||
    !identifier(command.snapshot_id) ||
    !command.snapshot_id.startsWith("synthetic:") ||
    typeof command.revision !== "string" ||
    !/^[0-9a-f]{40}$/.test(command.revision) ||
    !time(command.cutoff_ms)
  )
    throw Error("UNQUALIFIED_EXECUTION_RESULT");
  for (const [key, value] of Object.entries(selection))
    if (command[key] !== value) throw Error("SELECTION_BINDING_MISMATCH");
  return command;
}
export async function executeRegisteredStrategy({
  selection,
  token,
  inputDigest,
  requestId,
  fetcher = fetch,
  delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  maxPolls = 30,
  signal,
}) {
  const binding = validateExecutionSelection(selection);
  if (
    typeof token !== "string" ||
    !token.trim() ||
    token.length > 8192 ||
    !digest(inputDigest) ||
    !uuid(requestId)
  )
    throw Error("OPERATOR_REGISTRATION_REQUIRED");
  if (!Number.isInteger(maxPolls) || maxPolls < 1 || maxPolls > 30)
    throw Error("INVALID_POLL_BUDGET");
  async function request(path, body) {
    let response;
    try {
      response = await fetcher(path, {
        method: body ? "POST" : "GET",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
        credentials: "omit",
        cache: "no-store",
        signal: signal
          ? AbortSignal.any([signal, AbortSignal.timeout(20000)])
          : AbortSignal.timeout(20000),
      });
    } catch {
      throw Error("EXECUTION_TRANSPORT_UNAVAILABLE");
    }
    if (!response.ok) throw Error(`HTTP_${response.status}`);
    if (Number(response.headers.get("content-length")) > 64000)
      throw Error("UNBOUNDED_EXECUTION_RESPONSE");
    const raw = await response.text();
    if (raw.length > 64000) throw Error("UNBOUNDED_EXECUTION_RESPONSE");
    try {
      return JSON.parse(raw);
    } catch {
      throw Error("INVALID_EXECUTION_RESPONSE");
    }
  }
  const base = "/api/control/research-executions";
  const job = await request(base, {
    schema_version: "tester.research-execution/1",
    request_id: requestId,
    input_digest: inputDigest,
  });
  if (!identifier(job?.job_id)) throw Error("INVALID_EXECUTION_JOB");
  for (let attempt = 0; attempt < maxPolls; attempt++) {
    if (signal?.aborted) throw Error("EXECUTION_CANCELLED");
    const result = await request(`${base}/${encodeURIComponent(job.job_id)}`);
    if (result?.job_id !== job.job_id) throw Error("EXECUTION_JOB_MISMATCH");
    if (result.state === "complete")
      return validateExecutionCommand(result, binding, job.job_id);
    if (!["pending", "running"].includes(result.state))
      throw Error("EXECUTION_NOT_COMPLETE");
    if (attempt + 1 < maxPolls) await delay(200);
  }
  throw Error("EXECUTION_PENDING_TIMEOUT");
}
