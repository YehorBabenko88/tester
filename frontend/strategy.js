import {
  executeRegisteredStrategy,
  validateExecutionSelection,
} from "./strategy-api.js";
import { mountResearchViewer } from "./research-view.js";
import { researchViewMarkup } from "./research-view-markup.js";
// No simulation or financial computation lives in this workspace.
export function mountStrategyWorkspace(
  root,
  { getSelection = () => null } = {},
) {
  const workspace = document.createElement("article");
  workspace.className = "chart-panel strategy-execution";
  workspace.innerHTML = `
    <h3>Operator-registered strategy simulation</h3>
    <p>SYNTHETIC ONLY · Real market coverage NOT PROVEN. No orders. The operator must register an immutable validated strategy + snapshot pair and grant execution access. Real downloaded history is NOT admitted here.</p>
    <form id="strategy-execution-form">
      <button id="strategy-bind" type="button">Use selected instrument and UTC range</button>
      <p id="strategy-binding" role="status">DATA_UNAVAILABLE · Select an instrument and range.</p>
      <label>Registered immutable input digest<input id="strategy-input-digest" maxlength="64" autocomplete="off" spellcheck="false" required /></label>
      <label>Execution capability<input id="strategy-capability" type="password" maxlength="8192" autocomplete="off" spellcheck="false" required /></label>
      <button id="strategy-run" type="submit" disabled>Request authorized synthetic run</button>
    </form>
    <p id="strategy-execution-status" role="status">DATA_UNAVAILABLE · No registered execution requested.</p>
    ${researchViewMarkup}`;
  root.append(workspace);
  const $ = (selector) => workspace.querySelector(selector);
  const viewer = mountResearchViewer(workspace, {
    isCurrent: () => {
      try {
        const current = validateExecutionSelection(getSelection());
        return (
          binding &&
          Object.keys(binding).every((key) => binding[key] === current[key])
        );
      } catch {
        return false;
      }
    },
  });
  let binding = null,
    busy = false,
    abort = null,
    requestId = null;
  const invalidate = () => {
    abort?.abort();
    viewer.clear();
  };
  const eligibility = () => {
    $("#strategy-run").disabled =
      busy ||
      !binding ||
      !/^[0-9a-f]{64}$/.test($("#strategy-input-digest").value) ||
      !$("#strategy-capability").value.trim();
  };
  $("#strategy-bind").addEventListener("click", () => {
    invalidate();
    requestId = null;
    try {
      const selected = getSelection();
      binding = validateExecutionSelection(selected);
      $("#strategy-binding").textContent =
        `${binding.category} / ${binding.symbol} / ${binding.interval} · UTC [${binding.start_ms}, ${binding.end_ms}) · catalog revision ${selected.catalog_revision || "UNKNOWN"} (not dataset revision). Real coverage UNKNOWN; server-registered synthetic snapshot revision is required.`;
    } catch {
      binding = null;
      $("#strategy-binding").textContent =
        "DATA_UNAVAILABLE · Select a valid instrument and UTC range.";
    }
    eligibility();
  });
  for (const selector of ["#strategy-input-digest", "#strategy-capability"])
    $(selector).addEventListener("input", eligibility);
  $("#strategy-input-digest").addEventListener("input", () => {
    requestId = null;
  });
  $("#strategy-execution-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || $("#strategy-run").disabled) return;
    busy = true;
    invalidate();
    abort = new AbortController();
    eligibility();
    let token = $("#strategy-capability").value;
    $("#strategy-capability").value = "";
    $("#strategy-execution-status").textContent =
      "PENDING · Authorized registered synthetic execution requested. No result available yet.";
    const expected = { ...binding };
    requestId ||= crypto.randomUUID();
    try {
      const command = await executeRegisteredStrategy({
        selection: expected,
        token,
        inputDigest: $("#strategy-input-digest").value,
        requestId,
        signal: abort.signal,
      });
      const current = validateExecutionSelection(getSelection());
      if (Object.keys(expected).some((key) => expected[key] !== current[key]))
        throw Error("SELECTION_CHANGED");
      $("#strategy-execution-status").textContent =
        "SIMULATED · SYNTHETIC ONLY · Execution complete; validating persisted chart and render acknowledgement. Statistics and SL/TP remain unavailable without typed artifacts.";
      if (!(await viewer.load(command, token)))
        throw Error("ARTIFACT_UNAVAILABLE");
    } catch (error) {
      viewer.clear();
      $("#strategy-execution-status").textContent =
        `DATA_UNAVAILABLE · ${error.message}. No local fallback or real-market execution.`;
    } finally {
      token = "";
      busy = false;
      eligibility();
    }
  });
  const headings = [
    "Strategy preview",
    "Stop Loss / Take Profit",
    "Simulation journal",
    "Equity",
    "Drawdown",
    "Trade overlays",
    "Trader disclosures and hypotheses",
  ];
  for (const title of headings) {
    const card = document.createElement("article");
    card.className = "chart-panel";
    card.setAttribute("role", "region");
    card.setAttribute("aria-label", title);
    const h = document.createElement("h3");
    h.textContent = title;
    const status = document.createElement("p");
    status.className = "panel-message";
    status.textContent =
      "DATA_UNAVAILABLE · No validated simulation output is connected. A verified run, pinned market snapshot and execution evidence are required.";
    if (title === "Trader disclosures and hypotheses") {
      status.textContent =
        "DATA_UNAVAILABLE | NOT_INTEGRATED | Source permission UNKNOWN. No qualified trader source is connected. DISCLOSED source events and INFERRED hypotheses require separate labels and evidence. Anonymous market prints cannot identify a trader. Missing fees, SL/TP and identity remain UNKNOWN.";
    }
    card.append(h, status);
    root.append(card);
  }
}
