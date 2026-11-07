// No simulation or financial computation lives in this workspace.
export function mountStrategyWorkspace(root) {
  const headings = [
    "Strategy preview",
    "Stop Loss / Take Profit",
    "Simulation journal",
    "Equity",
    "Drawdown",
    "Trade overlays",
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
    card.append(h, status);
    root.append(card);
  }
}
