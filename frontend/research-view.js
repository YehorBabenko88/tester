import {
  ResearchChart,
  validateResearchChart,
  researchDigest,
} from "./research-chart.js";
export function mountResearchViewer(root, { isCurrent = () => true } = {}) {
  const $ = (s) => root.querySelector(s),
    status = $("#research-status"),
    canvas = $("#research-canvas");
  let active = null,
    loading = false,
    epoch = 0;
  const chart = new ResearchChart(canvas, () => {
    $("#research-range").textContent =
      `Visible UTC [${chart.range.join(", ")})`;
  });
  function clear() {
    epoch++;
    active = null;
    chart.clear();
    $("#research-range").textContent = "";
    $("#research-fills").replaceChildren();
    $("#research-provenance").textContent = "";
    delete canvas.dataset.artifactDigest;
    delete canvas.dataset.renderAck;
  }
  async function api(path, token, body) {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: {
        Accept: "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: AbortSignal.timeout(20000),
      cache: "no-store",
      credentials: "omit",
    });
    if (Number(response.headers.get("content-length")) > 8000000)
      throw Error("Research response too large");
    const raw = await response.text();
    if (raw.length > 8000000) throw Error("Research response too large");
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      throw Error("Invalid research response");
    }
    if (!response.ok)
      throw Error(
        `${response.status} ${data.detail?.message || data.detail || data.error?.message || "Research access denied"}`,
      );
    return data;
  }
  function table(artifact) {
    const body = $("#research-fills");
    body.replaceChildren();
    for (const m of artifact.markers) {
      const row = document.createElement("tr");
      row.dataset.fillId = m.fill_id;
      const focus = () => chart.setRange(m.window_start_ms, m.window_end_ms);
      row.tabIndex = 0;
      row.setAttribute("aria-label", `Inspect simulated fill ${m.fill_id}`);
      row.addEventListener("click", focus);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          focus();
        }
      });
      for (const value of [
        `${m.fill_id} / ${m.order_id} / ${m.position_id}`,
        `SIMULATED ${m.role} ${m.side}`,
        m.price,
        `${m.timestamp_ms} / ${m.known_at_ms}`,
        `${m.time_precision} [${m.window_start_ms}, ${m.window_end_ms})`,
        m.closed_trade_id ?? "NONE  |  no closed trade link",
      ]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
  }
  async function load(command, suppliedToken) {
    if (loading) return;
    loading = true;

    clear();
    const generation = epoch;
    const current = () => {
      if (generation !== epoch || !isCurrent())
        throw Error("Research selection changed");
    };
    status.textContent = "Reading authorized persisted synthetic research | ";
    let token = suppliedToken;
    try {
      if (
        command.schema_version !== "tester.research-command/1" ||
        command.action !== "chart.overlay" ||
        typeof command.expected_artifact_digest !== "string"
      )
        throw Error("Approved command binding required");
      const job = await api("/api/control/research", token, command);
      if (
        typeof job.job_id !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(job.job_id)
      )
        throw Error("Invalid research job identity");
      current();
      const path = `/api/control/research/${encodeURIComponent(job.job_id)}`;
      let result;
      for (let attempt = 0; attempt < 30; attempt++) {
        result = await api(path, token);
        current();
        if (result.state === "complete") break;
        if (["failed", "cancelled", "denied"].includes(result.state))
          throw Error(`Research job ${result.state}`);
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      if (result?.state !== "complete")
        throw Error("Research result not complete");
      const artifact = validateResearchChart(result.artifact);
      if (
        result.artifact_digest !== command.expected_artifact_digest ||
        (await researchDigest(artifact)) !== result.artifact_digest
      )
        throw Error("Artifact digest mismatch");
      for (const key of [
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
      ])
        if (artifact[key] !== command[key])
          throw Error(`Artifact binding mismatch: ${key}`);
      current();
      active = artifact;
      chart.set(artifact);
      if (
        !chart.lastGeometry ||
        chart.lastGeometry.markers.length !== artifact.markers.length
      )
        throw Error("Full-range render did not account for all fills");
      table(artifact);
      $("#research-provenance").textContent =
        `${artifact.symbol}  |  ${artifact.interval}  |  coverage ${artifact.coverage} (synthetic only)  |  ${artifact.source_ids.join(", ")}  |  run ${artifact.run_id}  |  snapshot ${artifact.snapshot_id}  |  revision ${artifact.revision}  |  artifact ${result.artifact_digest}`;
      const ids = chart.lastGeometry.markers.map((m) => m.source.fill_id);
      await api(`${path}/ack`, token, {
        artifact_digest: result.artifact_digest,
        rendered_fill_ids: ids,
      });
      current();
      canvas.dataset.artifactDigest = result.artifact_digest;
      canvas.dataset.renderAck = "accepted";
      status.textContent = `SIMULATED  |  SYNTHETIC ONLY  |  ${ids.length} persisted fills rendered and acknowledged. Real market coverage NOT PROVEN.`;
      return true;
    } catch (error) {
      if (generation !== epoch) return false;
      clear();
      status.textContent = `DATA_UNAVAILABLE  |  ${error.message}`;
      return false;
    } finally {
      token = "";
      loading = false;
    }
  }
  for (const [name, key] of [
    ["left", "ArrowLeft"],
    ["right", "ArrowRight"],
    ["in", "+"],
    ["out", "-"],
  ])
    $("#research-" + name).addEventListener("click", () =>
      canvas.dispatchEvent(new KeyboardEvent("keydown", { key })),
    );
  $("#research-reset").addEventListener("click", () => {
    if (active) chart.set(active);
  });

  return { load, clear };
}
