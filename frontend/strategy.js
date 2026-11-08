// No simulation or financial computation lives in this workspace.
export function mountStrategyWorkspace(root) {
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
