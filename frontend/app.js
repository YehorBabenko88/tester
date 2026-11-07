import { mountStrategyWorkspace } from "./strategy.js";
import { shiftRange } from "./time.js";
import { adaptSeries, sourceText } from "./series.js";
import { request } from "./api.js";
import {
  metric,
  compareDecimal,
  progress,
  validateSeries,
  PANELS,
} from "./model.js";
import { PanelChart } from "./charts.js";
const $ = (s) => document.querySelector(s);
const el = (tag, text, className) => {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (className) n.className = className;
  return n;
};
const readStore = (k, fallback) => {
  try {
    return JSON.parse(localStorage.getItem(k)) ?? fallback;
  } catch {
    return fallback;
  }
};
const save = (k, v) => {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    $("#download-status").textContent =
      "Local persistence unavailable. Keep this tab open.";
  }
};
const state = {
  selected: new Set(readStore("bybit-watchlist", [])),
  saved: new Set(readStore("bybit-watchlist", [])),
  rows: [],
  cursor: null,
  active: null,
  jobs: new Map(),
  ids: new Set(readStore("bybit-jobs", [])),
  direction: -1,
  charts: new Map(),
  chartAbort: null,
  instrumentAbort: null,
};
state.ids = new Set(
  [...state.ids]
    .filter((x) => typeof x === "string" && x.length <= 64)
    .slice(-100),
);
const jobVersions = new Map(),
  jobControls = new Set();
let polling = false;
const METRICS = [
  ["price", "Price"],
  ["volatility", "Volatility"],
  ["change_1h", "Price change 1h"],
  ["change_24h", "Price change 24h"],
  ["volume", "Volume"],
  ["turnover", "Turnover"],
  ["open_interest", "Open Interest"],
  ["oi_change_1h", "OI change 1h"],
  ["oi_change_24h", "OI change 24h"],
  ["market_cap", "Market capitalization"],
];
const iso = (t) =>
  Number.isSafeInteger(t) ? new Date(t).toISOString() : "Unknown";
const utcInput = (t) => new Date(t).toISOString().slice(0, 16);
$("#end").value = utcInput(Date.now());
$("#start").value = utcInput(Date.now() - 86400000);
function range() {
  const a = Date.parse($("#start").value + "Z"),
    b = Date.parse($("#end").value + "Z");
  if (!Number.isSafeInteger(a) || !Number.isSafeInteger(b) || a < 0 || a >= b)
    throw Error("Choose a valid UTC range: start < end (exclusive).");
  return [a, b];
}
function intervalMs() {
  return $("#interval").value;
}
function query() {
  const [start_ms, end_ms] = range();
  return {
    category: state.active?.category || $("#category").value,
    symbol: state.active?.symbol,
    start_ms,
    end_ms,
    interval: $("#interval").value,
    limit: 1000,
  };
}
function selection() {
  $("#selection-count").textContent = `${state.selected.size} selected`;
  $("#download").disabled = !state.selected.size;
}
function sourceTitle(m) {
  return `${m.availability} · ${m.source || "Source unknown"} · observed ${iso(m.as_of)} · ${m.unit || "unit unknown"}${m.reason ? " · " + m.reason : ""}${m.formula ? " · " + m.formula : ""}`;
}
function renderRows() {
  const filtered = state.rows.filter(
    (r) =>
      !$("#watchlist").checked || state.saved.has(`${r.category}:${r.symbol}`),
  );
  const scroll = $("#table-scroll"),
    rowHeight = 48,
    first = Math.min(
      Math.max(0, filtered.length - 1),
      Math.max(0, Math.floor((scroll.scrollTop - 35) / rowHeight) - 3),
    ),
    last = Math.min(filtered.length, first + 20),
    body = $("#rows");
  body.replaceChildren();
  const spacer = (h) => {
    const tr = el("tr");
    const td = el("td");
    td.colSpan = 12;
    td.style.cssText = `height:${h}px;padding:0;border:0`;
    tr.append(td);
    return tr;
  };
  if (first) body.append(spacer(first * rowHeight));
  for (const r of filtered.slice(first, last)) {
    const key = `${r.category}:${r.symbol}`,
      tr = el("tr");
    tr.dataset.row = key;
    const td = el("td"),
      check = el("input");
    check.type = "checkbox";
    check.setAttribute("aria-label", `Select ${r.symbol}`);
    check.checked = state.selected.has(key);
    check.addEventListener("change", () => {
      check.checked ? state.selected.add(key) : state.selected.delete(key);
      selection();
    });
    td.append(check);
    tr.append(td);
    const name = el("td"),
      button = el("button", r.symbol);
    button.setAttribute("aria-label", `Chart ${r.symbol}`);
    button.addEventListener("click", () => {
      state.active = r;
      $("#active-symbol").textContent = `${r.symbol} · ${r.category}`;
      loadCharts();
    });
    name.append(
      button,
      el("small", `${r.category} · ${r.status || "status unknown"}`),
    );
    tr.append(name);
    for (const [key, label] of METRICS) {
      const m = metric(r.metrics?.[key]),
        cell = el(
          "td",
          m.value == null ? "—" : `${m.value}${m.unit === "%" ? "%" : ""}`,
          m.value == null
            ? "unavailable"
            : m.availability === "PARTIAL"
              ? "partial"
              : "",
        );
      cell.title = `${label}: ${sourceTitle(m)}`;
      cell.setAttribute(
        "aria-label",
        `${label}: ${m.value ?? "DATA_UNAVAILABLE"}. ${sourceTitle(m)}`,
      );
      tr.append(cell);
    }
    body.append(tr);
  }
  if (last < filtered.length)
    body.append(spacer((filtered.length - last) * rowHeight));
  selection();
}
let searchTimer;
async function loadInstruments(cursor = null) {
  state.instrumentAbort?.abort();
  const abort = new AbortController();
  state.instrumentAbort = abort;
  $("#screener-status").textContent = "Loading instruments…";
  $("#next").disabled = true;
  if (!cursor) {
    state.rows = [];
    $("#table-scroll").scrollTop = 0;
    renderRows();
  }
  try {
    const p = await request(
      "/api/instruments",
      {
        category: $("#category").value,
        search: $("#search").value.toUpperCase(),
        sort: $("#sort").value,
        direction: state.direction === -1 ? "desc" : "asc",
        limit: 1000,
        cursor,
      },
      { signal: abort.signal },
    );
    const rows = Array.isArray(p.data) ? p.data : p.data.instruments;
    if (!Array.isArray(rows) || rows.length > 1000)
      throw Error("Corrupt instrument page");
    if (
      rows.some(
        (r) =>
          !r ||
          typeof r.symbol !== "string" ||
          !/^[A-Z0-9_-]{1,40}$/.test(r.symbol) ||
          !["linear", "spot", "inverse", null, undefined].includes(r.category),
      )
    )
      throw Error("Corrupt instrument identity");
    // Local order is only within the bounded page until the provider implements global sorting.
    const normalized = rows
      .map((r) => ({ ...r, category: r.category || $("#category").value }))
      .filter((r) => r.symbol.includes($("#search").value.toUpperCase()));
    const key = $("#sort").value;
    normalized.sort((a, b) =>
      key === "symbol"
        ? a.symbol.localeCompare(b.symbol) * state.direction
        : compareDecimal(
            metric(a.metrics?.[key]).value,
            metric(b.metrics?.[key]).value,
            state.direction,
          ),
    );
    state.rows = normalized;
    state.cursor = p.next_cursor || null;
    $("#table-scroll").scrollTop = 0;
    $("#screener-status").textContent = normalized.length
      ? `${normalized.length} instruments · bounded page · sorting applies to this page unless provider supports global sorting`
      : "No instruments match. No data has been fabricated.";
    $("#next").disabled = !state.cursor;
    renderRows();
  } catch (e) {
    if (e.name === "AbortError") return;
    state.rows = [];
    state.cursor = null;
    renderRows();
    $("#screener-status").textContent = e.message;
  }
}
$("#table-scroll").addEventListener("scroll", renderRows);
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadInstruments(), 180);
});
for (const id of ["category", "sort"])
  $("#" + id).addEventListener("change", () => loadInstruments());
$("#category").addEventListener("change", loadSources);
$("#direction").onclick = () => {
  state.direction *= -1;
  $("#direction").textContent = state.direction === -1 ? "↓ Desc" : "↑ Asc";
  loadInstruments();
};
$("#refresh").onclick = () => loadInstruments();
$("#next").onclick = () => loadInstruments(state.cursor);
$("#watchlist").onchange = () => {
  $("#table-scroll").scrollTop = 0;
  renderRows();
};
$("#clear").onclick = () => {
  state.selected.clear();
  renderRows();
};
$("#save").onclick = () => {
  state.saved = new Set(state.selected);
  save("bybit-watchlist", [...state.selected]);
  $("#screener-status").textContent = "Watchlist saved on this device.";
};
async function loadSources() {
  try {
    const p = await request("/api/sources", {
      category: $("#category").value,
      limit: 1000,
    });
    if (!Array.isArray(p.data) || p.data.length > 1000)
      throw Error("Corrupt source list");
    const select = $("#source");
    select.replaceChildren();
    for (const s of p.data) {
      if (!/^[a-z0-9_-]{1,64}$/.test(s.id)) continue;
      const option = el("option", `${s.id} · ${s.availability}`);
      option.value = s.id;
      option.disabled = !["available", "partial"].includes(s.availability);
      option.dataset.kinds = JSON.stringify(s.kinds);
      select.append(option);
    }
    if (!select.options.length) {
      const option = el("option", "No available sources");
      option.value = "";
      select.append(option);
    }
  } catch (e) {
    $("#source").replaceChildren(el("option", "Sources unavailable"));
    $("#source").firstChild.value = "";
    $("#download-status").textContent = e.message;
  }
}
const jobState = (j) =>
  ({
    queued: "DISCOVERING",
    running: "DOWNLOADING",
    paused: "PAUSED",
    completed: "COMPLETE",
    failed: "FAILED",
  })[j.state] ||
  j.state ||
  "UNKNOWN";
function jobProgress(j) {
  if (typeof j.progress === "number") {
    return {
      percent: Number.isFinite(j.progress)
        ? Math.min(
            j.verified === true && jobState(j) === "COMPLETE" ? 100 : 99,
            Math.max(0, Math.floor(j.progress * 100)),
          )
        : null,
    };
  }
  return progress({ ...j, state: jobState(j) });
}
function renderJobs() {
  const jobs = $("#jobs");
  jobs.replaceChildren();
  const all = [...state.jobs.values()];
  let complete = 0;
  for (const j of all) {
    const box = el("article", null, "job"),
      head = el("div", null, "job-head");
    head.append(
      el(
        "strong",
        `${j.request?.symbol || j.symbol || "Instrument unknown"} · ${j.request?.source || "Sources below"}`,
      ),
      el("span", jobState(j), "badge"),
    );
    box.append(head);
    const p = jobProgress(j),
      bar = el("progress");
    bar.max = 100;
    if (p.percent != null) bar.value = p.percent;
    bar.setAttribute("aria-label", `Progress ${j.id}`);
    box.append(
      bar,
      el(
        "p",
        p.percent == null
          ? "Unknown total"
          : `${p.percent}% · ${j.verified === true || j.progress?.verified === true ? "verification supplied" : "verification not confirmed"}`,
      ),
    );
    if (p.percent === 100) complete++;
    const counters = j.counters || {};
    box.append(
      el(
        "p",
        `Bytes: ${counters.bytes ?? "unknown"} · Verified records: ${counters.verified_records ?? "unknown"} · Speed: ${counters.bytes_per_second ?? "unknown"} B/s · ETA: ${counters.eta_seconds != null && counters.eta_confident === true ? counters.eta_seconds + " s" : "not enough evidence"}`,
      ),
    );
    const sources = j.sources || [
      {
        source: j.request?.source || "unknown",
        state: jobState(j),
        error: j.error,
      },
    ];
    for (const s of sources) {
      const row = el("div", null, "job-source");
      row.append(
        el("strong", `${s.source} · ${s.state || "UNKNOWN"}`),
        el(
          "div",
          `Bytes ${s.bytes ?? "unknown"} · Verified ${s.verified_records ?? "unknown"} · Speed ${s.bytes_per_second ?? "unknown"} B/s`,
        ),
      );
      const sp = jobProgress(s),
        sb = el("progress");
      sb.max = 100;
      if (sp.percent != null) sb.value = sp.percent;
      sb.setAttribute("aria-label", "Source progress " + s.source);
      row.append(
        sb,
        el("span", sp.percent == null ? "Unknown total" : sp.percent + "%"),
      );
      if (s.error) row.append(el("div", String(s.error), "error"));
      box.append(row);
    }
    if (j.error) box.append(el("p", j.error, "error"));
    const controls = el("div", null, "job-actions");
    for (const action of ["pause", "resume", "retry"]) {
      const button = el("button", action[0].toUpperCase() + action.slice(1));
      button.setAttribute("aria-label", `${button.textContent} ${j.id}`);
      button.disabled =
        action === "pause"
          ? !["DISCOVERING", "DOWNLOADING", "VERIFYING"].includes(jobState(j))
          : action === "resume"
            ? jobState(j) !== "PAUSED"
            : !["FAILED", "PARTIAL"].includes(jobState(j));
      button.onclick = async () => {
        button.disabled = true;
        jobControls.add(j.id);
        jobVersions.set(j.id, (jobVersions.get(j.id) || 0) + 1);
        try {
          const p = await request(
            `/api/downloads/${encodeURIComponent(j.id)}/${action}`,
            {},
            { method: "POST" },
          );
          state.jobs.set(j.id, p.data);
          renderJobs();
        } catch (e) {
          $("#download-status").textContent = e.message;
          button.disabled = false;
        } finally {
          jobControls.delete(j.id);
          jobVersions.set(j.id, (jobVersions.get(j.id) || 0) + 1);
        }
      };
      controls.append(button);
    }
    box.append(controls);
    jobs.append(box);
  }
  $("#overall").textContent = all.length
    ? `${complete}/${all.length} jobs verified complete · ${all.length - complete} require completion or verification`
    : "";
}
async function refreshJobs(force = false) {
  if (polling) return;
  polling = true;
  const ids = [...state.ids].filter(
    (id) =>
      !jobControls.has(id) &&
      (force ||
        !["COMPLETE", "FAILED", "PARTIAL"].includes(
          jobState(state.jobs.get(id) || {}),
        )),
  );
  let cursor = 0;
  try {
    await Promise.allSettled(
      Array.from({ length: Math.min(4, ids.length) }, async () => {
        while (cursor < ids.length) {
          const id = ids[cursor++],
            version = jobVersions.get(id) || 0;
          try {
            const p = await request("/api/downloads/" + encodeURIComponent(id));
            if (!p.data?.id) throw Error("Corrupt job");
            if ((jobVersions.get(id) || 0) === version)
              state.jobs.set(id, p.data);
          } catch (e) {
            $("#download-status").textContent = `Job ${id}: ${e.message}`;
          }
        }
      }),
    );
    renderJobs();
  } finally {
    polling = false;
  }
}
$("#refresh-jobs").onclick = () => refreshJobs(true);
$("#download").onclick = async () => {
  const button = $("#download");
  button.disabled = true;
  try {
    const [start_ms, end_ms] = range(),
      source = $("#source").value,
      kind = $("#kind").value;
    if (!source)
      throw Error(
        "DATA_UNAVAILABLE: no connected source. Retry source discovery.",
      );
    const kinds = JSON.parse(
      $("#source").selectedOptions[0].dataset.kinds || "[]",
    );
    if (!kinds.includes(kind))
      throw Error("Selected source does not support " + kind);
    if (state.selected.size > 100)
      throw Error("Choose at most 100 instruments per batch.");
    const errors = [];
    for (const key of [...state.selected]) {
      const [category, symbol] = key.split(":");
      try {
        if (state.ids.size >= 100) {
          const old = [...state.ids].find((id) =>
            ["COMPLETE", "FAILED", "PARTIAL"].includes(
              jobState(state.jobs.get(id) || {}),
            ),
          );
          if (!old)
            throw Error(
              "100 tracked jobs reached. Wait for a job to finish before adding more.",
            );
          state.ids.delete(old);
          state.jobs.delete(old);
        }
        const p = await request(
          "/api/downloads",
          {},
          {
            method: "POST",
            body: {
              category,
              symbol,
              source,
              kind,
              interval: $("#interval").value,
              start_ms,
              end_ms,
            },
          },
        );
        if (!p.data?.id) throw Error("Corrupt job response");
        state.ids.add(p.data.id);
        if (state.ids.size > 100) {
          const oldest = [...state.ids].find((id) =>
            ["COMPLETE", "FAILED", "PARTIAL"].includes(
              jobState(state.jobs.get(id) || {}),
            ),
          );
          if (oldest) {
            state.ids.delete(oldest);
            state.jobs.delete(oldest);
          }
        }
        save("bybit-jobs", [...state.ids]);
        state.jobs.set(p.data.id, p.data);
        renderJobs();
      } catch (e) {
        errors.push(symbol + ": " + e.message);
      }
    }
    $("#download-status").textContent = errors.length
      ? errors.join("\n")
      : "Jobs accepted. Progress will be polled from the local API.";
  } catch (e) {
    $("#download-status").textContent = e.message;
  } finally {
    selection();
  }
};
// No raw-trade endpoint is ever requested by charts.
function panelEndpoint(kind) {
  return (
    {
      candles: "/api/candles",
      volume: "/api/candles",
      footprint: "/api/footprint",
      open_interest: "/api/open-interest",
      funding: "/api/funding",
    }[kind] || "/api/analytics"
  );
}
for (const [kind, title, type] of PANELS) {
  const section = el("article", null, "chart-panel");
  section.dataset.panel = kind;
  const head = el("div", null, "panel-head");
  const originBadge = el("span", "origin unknown", "badge");
  head.append(el("h3", title), originBadge);
  const message = el(
      "div",
      "DATA_UNAVAILABLE · Select an instrument",
      "panel-message",
    ),
    meta = el("div", null, "panel-meta"),
    wrap = el("div", null, "canvas-wrap"),
    canvas = el("canvas");
  canvas.setAttribute("aria-label", title + " chart");
  canvas.setAttribute("role", "img");
  wrap.append(canvas);
  wrap.hidden = true;
  const details = el("details");
  const evidence = el("details", null, "panel-evidence"),
    proof = el("pre");
  evidence.append(
    el("summary", "Availability, coverage & source evidence"),
    proof,
  );
  details.append(el("summary", "Exact source values (first 100 rows)"));
  const paging = el("div", null, "panel-paging"),
    previous = el("button", "Previous data page"),
    next = el("button", "Next data page"),
    pageLabel = el("span", "Page 1");
  previous.disabled = true;
  next.disabled = true;
  paging.append(previous, pageLabel, next);
  section.append(head, meta, message, wrap, paging, evidence, details);
  $("#panels").append(section);
  state.charts.set(kind, {
    section,
    message,
    meta,
    wrap,
    details,
    proof,
    originBadge,
    previous,
    next,
    pageLabel,
    page: { cursor: null, back: [], next: null },
    chart: new PanelChart(canvas, kind, setRange, hover),
  });
  previous.onclick = () => {
    const p = state.charts.get(kind);
    if (!p.page.back.length) return;
    p.page.cursor = p.page.back.pop();
    loadCharts({ keepPages: true });
  };
  next.onclick = () => {
    const p = state.charts.get(kind),
      token = p.page.next;
    if (!token) return;
    if (
      p.page.back.length >= 100 ||
      token === p.page.cursor ||
      p.page.back.includes(token)
    ) {
      p.message.hidden = false;
      p.message.textContent =
        "Cursor history limit or cycle: change range to continue safely.";
      next.disabled = true;
      return;
    }
    p.page.back.push(p.page.cursor);
    p.page.cursor = token;
    loadCharts({ keepPages: true });
  };
}
function hover(ts) {
  $("#crosshair").textContent = ts == null ? "—" : iso(ts);
  for (const p of state.charts.values()) p.chart.hover(ts);
}
let rangeTimer;
function setRange(a, b) {
  if (
    !Number.isFinite(a) ||
    !Number.isFinite(b) ||
    b - a < 60000 ||
    b > 253402300799000
  )
    return;
  $("#start").value = utcInput(a);
  $("#end").value = utcInput(b);
  clearTimeout(rangeTimer);
  rangeTimer = setTimeout(loadCharts, 120);
}
async function loadCharts(options = {}) {
  if (!state.active) {
    $("#chart-status").textContent = "Select an instrument from the screener.";
    return;
  }
  state.chartAbort?.abort();
  const abort = new AbortController();
  state.chartAbort = abort;
  let q;
  try {
    q = query();
  } catch (e) {
    $("#chart-status").textContent = e.message;
    return;
  }
  $("#chart-status").textContent = "Loading bounded server aggregates…";
  $("#range-label").textContent =
    `${iso(q.start_ms)} → ${iso(q.end_ms)} (exclusive) · ${q.interval} · max 1000 points/panel`;
  for (const p of state.charts.values()) {
    const queryKey = JSON.stringify(q);
    if (!options.keepPages || p.page.queryKey !== queryKey)
      p.page = { cursor: null, back: [], next: null, queryKey };
    p.previous.disabled = true;
    p.next.disabled = true;
    p.message.textContent = "Loading…";
    p.message.hidden = false;
    p.wrap.hidden = true;
    p.meta.textContent = "";
    p.proof.textContent = "";
    p.details.replaceChildren(
      el("summary", "Exact source values (first 100 rows)"),
    );
  }
  const cache = new Map();
  let unavailable = 0;
  await Promise.allSettled(
    PANELS.map(async ([kind, , type]) => {
      const p = state.charts.get(kind);
      try {
        const path = panelEndpoint(kind),
          params = {
            ...q,
            ...(p.page.cursor ? { cursor: p.page.cursor } : {}),
            ...(path === "/api/analytics" ? { metric: kind } : {}),
          },
          cacheKey = path + JSON.stringify(params);
        if (!cache.has(cacheKey))
          cache.set(cacheKey, request(path, params, { signal: abort.signal }));
        const payload = await cache.get(cacheKey);
        if (abort.signal.aborted) return;
        if (
          payload.next_cursor != null &&
          (typeof payload.next_cursor !== "string" ||
            payload.next_cursor.length > 4096)
        )
          throw Error("Corrupt server cursor");
        const series = adaptSeries(payload, kind, q);
        p.page.next = payload.next_cursor || null;
        p.next.disabled = !p.page.next;
        p.previous.disabled = !p.page.back.length;
        p.pageLabel.textContent = `Page ${p.page.back.length + 1} · ${series.source_rows.length} records (≤1000)`;
        p.originBadge.textContent = series.data_type;
        p.meta.textContent = `${series.availability} · ${(series.source || "source unknown").slice(0, 240)} · as of ${iso(series.as_of)} · completeness ${series.completeness || "UNKNOWN"} · ${series.data_type}`;
        p.proof.textContent = JSON.stringify(
          {
            availability: series.availability,
            reason: series.reason,
            coverage: series.coverage,
            provenance: series.provenance,
          },
          null,
          2,
        );
        if (!series.renderable) {
          unavailable++;
          p.message.textContent =
            "DATA_UNAVAILABLE · " + series.availability + " · " + series.reason;
          return;
        }
        p.message.textContent = series.reason || "";
        p.message.hidden = !series.reason;
        p.wrap.hidden = false;
        p.chart.set(series.rows, [q.start_ms, q.end_ms], intervalMs());
        if (kind === "candles") {
          const open = series.rows.filter((r) => r.is_closed === false).length,
            unknown = series.rows.filter(
              (r) => typeof r.is_closed !== "boolean",
            ).length;
          p.meta.textContent += ` · unclosed ${open} · closure unknown ${unknown}`;
        }
        const table = el("table");
        for (const r of series.rows.slice(0, 100)) {
          const tr = el("tr");
          tr.append(el("td", iso(r.ts)), el("td", JSON.stringify(r)));
          table.append(tr);
        }
        p.details.append(table);
      } catch (e) {
        if (e.name === "AbortError") return;
        unavailable++;
        p.message.textContent = "DATA_UNAVAILABLE · " + e.message;
      }
    }),
  );
  if (!abort.signal.aborted) {
    $("#chart-status").textContent =
      `${PANELS.length - unavailable}/${PANELS.length} panels have records. Consult each panel for coverage and verification.`;
    loadQuality(q, abort.signal);
  }
}
async function loadQuality(q, signal) {
  $("#quality-data").textContent = "Loading coverage…";
  try {
    const p = await request("/api/coverage", q, { signal });
    if (signal.aborted) return;
    if (!Array.isArray(p.data) || p.data.length > 1000)
      throw Error("Corrupt coverage response");
    const box = $("#quality-data");
    box.replaceChildren();
    if (!p.data.length)
      box.append(
        el(
          "div",
          "UNKNOWN · No coverage evidence returned. Empty data does not prove completeness.",
          "quality-row",
        ),
      );
    for (const c of p.data) {
      const row = el(
        "div",
        `${c.status || "UNKNOWN"} · ${c.kind || "kind unknown"} · ${iso(c.start_ms)} → ${iso(c.end_ms)} · ${sourceText(c.provenance) || "source unknown"}`,
        "quality-row",
      );
      box.append(row);
    }
    box.append(
      el(
        "div",
        `Coverage pages: ${p.next_cursor ? "PARTIAL — more evidence exists; narrow the range" : "end of response"} · History depth: ${p.data.length ? "only returned coverage ranges are confirmed" : "unknown"} · Archive verification: not supplied unless explicitly stated by source · Raw integrity is distinct from derived correctness.`,
        "quality-row",
      ),
    );
  } catch (e) {
    if (e.name !== "AbortError")
      $("#quality-data").textContent = "DATA_UNAVAILABLE · " + e.message;
  }
}
$("#apply").onclick = loadCharts;
$("#interval").onchange = () => {
  if (state.active) loadCharts();
};
for (const [id, factor, shift] of [
  ["zoom-in", 0.5, 0],
  ["zoom-out", 2, 0],
  ["pan-left", 1, -0.5],
  ["pan-right", 1, 0.5],
])
  $("#" + id).onclick = () => {
    try {
      const [a, b] = range();
      if (shift && ["W", "M"].includes($("#interval").value)) {
        const moved = shiftRange(
          [a, b],
          $("#interval").value,
          shift < 0 ? -1 : 1,
        );
        setRange(...moved);
        return;
      }
      const span = b - a,
        mid = (a + b) / 2 + span * shift;
      setRange(
        Math.max(0, Math.round(mid - (span * factor) / 2)),
        Math.round(mid + (span * factor) / 2),
      );
    } catch (e) {
      $("#chart-status").textContent = e.message;
    }
  };
setInterval(() => {
  if (!document.hidden) refreshJobs();
}, 2500);
setInterval(
  () =>
    ($("#clock").textContent =
      new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC"),
  1000,
);
selection();
loadInstruments();
loadSources();
refreshJobs();

mountStrategyWorkspace(document.querySelector("#strategy-panels"));
