// Presentation only: exact producer values, no return/risk/statistics calculation.
export function renderResearchSummary(root, summary, focus) {
  root.replaceChildren();
  const el = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  root.append(el("h3", "SIMULATED statistics and history · SYNTHETIC ONLY"));
  root.append(
    el(
      "p",
      `Producer ${summary.source_sha} · ${summary.engine_version} · run ${summary.run_id} · input ${summary.input_digest} · artifact ${summary.artifact_digest}. Real market coverage NOT PROVEN.`,
    ),
  );
  root.append(
    el(
      "p",
      `Confidence ${summary.metrics.confidence.method}: ${summary.metrics.confidence.reason}`,
    ),
  );
  root.append(
    el(
      "p",
      "Dashed SIMULATED SL/TP lines cover only the recorded entry uncertainty window, not position lifetime. Brackets below are timestamped observations; no trailing horizon is inferred.",
    ),
  );
  function table(title, fields, rows, onRow) {
    const wrap = el("div");
    wrap.className = "research-table";
    const t = el("table"),
      caption = el("caption", title),
      head = el("thead"),
      tr = el("tr");
    for (const field of fields) tr.append(el("th", field));
    head.append(tr);
    const body = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      for (const field of fields)
        tr.append(
          el(
            "td",
            row[field] === null
              ? "UNKNOWN — not estimated by producer"
              : row[field],
          ),
        );
      if (onRow) {
        tr.tabIndex = 0;
        tr.setAttribute("aria-label", `Inspect simulated trade ${row.id}`);
        tr.addEventListener("click", () => onRow(row));
        tr.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onRow(row);
          }
        });
      }
      body.append(tr);
    }
    t.append(caption, head, body);
    wrap.append(t);
    root.append(wrap);
  }
  table(
    "Producer statistics · exact values; ratios are unscaled",
    ["metric", "value"],
    Object.entries(summary.metrics)
      .filter(([name]) => name !== "confidence")
      .map(([metric, value]) => ({ metric, value })),
  );
  table(
    "Closed simulated trades · click for entry uncertainty window",
    [
      "id",
      "side",
      "quantity",
      "entry_fill_id",
      "exit_fill_id",
      "entry_timestamp_ms",
      "exit_timestamp_ms",
      "entry_price",
      "exit_price",
      "stop_loss",
      "take_profit",
      "gross_pnl",
      "net_pnl",
      "fees",
      "funding_costs",
      "reason",
    ],
    summary.trades,
    (t) => focus(t.entry_fill_id),
  );
  table(
    "Producer equity history · UTC ms and exact values",
    ["timestamp_ms", "cash", "equity", "margin"],
    summary.equity_curve,
  );
  table(
    "Open-position bracket observations · points only, not holding intervals",
    [
      "timestamp_ms",
      "entry_fill_id",
      "stop_loss",
      "take_profit",
      "trailing_stop",
    ],
    summary.brackets,
  );
}
