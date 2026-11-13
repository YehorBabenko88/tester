import {
  CATALOG_METRICS,
  catalogMetric,
  nextSort,
  jobsForRow,
} from "./catalog-ui.js";
import { mountStrategyWorkspace } from "./strategy.js";
import { shiftRange } from "./time.js";
import { adaptSeries, sourceText } from "./series.js";
import { request } from "./api.js";
import { progress, connectionNotice, validateSeries, PANELS } from "./model.js";
import { PanelChart } from "./charts.js";
import { classifyPanelCapability } from "./capabilities.js";
const $ = (s) => document.querySelector(s);
const el = (tag, text, className) => {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (className) n.className = className;
  return n;
};
const connection = connectionNotice(window.location);
if (connection) {
  const notice = el(
    "p",
    connection.text,
    connection.warning ? "connection-notice error" : "connection-notice",
  );
  notice.id = "connection-notice";
  if (connection.warning) notice.setAttribute("role", "alert");
  document.querySelector("main").prepend(notice);
}
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
  selected: new Set(
    readStore("bybit-selected", readStore("bybit-watchlist", [])),
  ),
  saved: new Set(readStore("bybit-watchlist", [])),
  rows: [],
  active: null,
  jobs: new Map(),
  jobRequests: readStore("bybit-job-requests", {}),
  ids: new Set(readStore("bybit-jobs", [])),
  direction: "none",
  sort: "symbol",
  offset: 0,
  total: 0,
  revision: null,
  catalog: {},
  charts: new Map(),
  chartAbort: null,
  instrumentAbort: null,
  capabilities: null,
  capabilityKey: null,
  capabilityError: null,
};
state.ids = new Set(
  [...state.ids]
    .filter((x) => typeof x === "string" && x.length <= 64)
    .slice(-100),
);
const jobVersions = new Map(),
  jobControls = new Set();
let polling = false;
const METRICS = CATALOG_METRICS;
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
  save("bybit-selected", [...state.selected]);
  $("#selection-count").textContent = `${state.selected.size} selected`;
  $("#download").disabled = !state.selected.size;
}
function sourceTitle(m) {
  return `${m.availability} · ${m.source || "Source unknown"} · observed ${iso(m.as_of)} · ${m.unit || "unit unknown"}${m.reason ? " · " + m.reason : ""}${m.formula ? " · " + m.formula : ""}`;
}
function renderRows() {
  const filtered = state.rows;
  const body = $("#rows");
  const focus = body.contains(document.activeElement)
    ? document.activeElement.getAttribute("aria-label")
    : null;
  const expanded = new Set(
    [...body.querySelectorAll("details[open]")].map((d) => d.dataset.jobId),
  );
  body.replaceChildren();
  for (const r of filtered) {
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
      const m = catalogMetric(r.metrics?.[key]),
        cell = el(
          "td",
          m.value == null
            ? "UNAVAILABLE"
            : `${m.value}${m.unit === "%" ? "%" : ""}`,
          m.value == null
            ? "unavailable"
            : ["PARTIAL", "STALE"].includes(m.availability)
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
    for (const field of [
      "category",
      "status",
      "contract_kind",
      "contract_type",
      "settle_coin",
    ])
      tr.append(el("td", r[field] || "UNAVAILABLE"));
    const action = el("td", null, "inline-download"),
      download = el("button", "Download");
    download.setAttribute("aria-label", `Download ${r.symbol}`);
    download.onclick = () => submitDownloads([key]);
    action.append(download);
    for (const j of jobsForRow([...state.jobs.values()], key)) {
      const p = jobProgress(j),
        detail = el("details"),
        summary = el(
          "summary",
          `Download ${jobState(j)} | Coverage ${p.coverage} | ${p.bytes_received ?? "unknown"} bytes | ${p.records_ingested ?? "unknown"} records | ${p.bytes_per_second ?? "unknown"} B/s`,
        ),
        bar = el("progress");
      detail.dataset.jobId = j.id;
      detail.open = expanded.has(j.id);
      bar.max = 100;
      if (p.percent != null) bar.value = p.percent;
      bar.setAttribute("aria-label", `Inline progress ${j.id}`);
      detail.append(
        summary,
        el(
          "small",
          `${j.id} | ${j.request?.source || p.source} | ${j.request?.interval || "unknown"} | ${iso(j.request?.start_ms)} | ${iso(j.request?.end_ms)}`,
        ),
        el(
          "small",
          `${p.bytes_received ?? "unknown"} bytes | ${p.records_ingested ?? "unknown"} records | ${p.bytes_per_second ?? "unknown"} B/s`,
        ),
      );
      action.append(bar, detail);
    }
    tr.append(action);
    body.append(tr);
  }
  if (focus)
    [...body.querySelectorAll("[aria-label]")]
      .find((n) => n.getAttribute("aria-label") === focus)
      ?.focus({ preventScroll: true });
  selection();
}
const FILTERS = [
  "search",
  "category",
  "contract-kind",
  "market-status",
  "watchlist",
];
const persisted = readStore("bybit-catalog-view", {});
for (const id of FILTERS)
  if (Object.hasOwn(persisted, id)) {
    if (id === "watchlist") $("#" + id).checked = persisted[id] === true;
    else $("#" + id).value = persisted[id];
  }
state.sort = persisted.sort || "symbol";
state.direction = persisted.direction || "none";
function persistView() {
  const values = Object.fromEntries(
    FILTERS.map((id) => [
      id,
      id === "watchlist" ? $("#" + id).checked : $("#" + id).value,
    ]),
  );
  save("bybit-catalog-view", {
    ...values,
    sort: state.sort,
    direction: state.direction,
  });
}
const HEADERS = [
  ["symbol", "Instrument"],
  ...METRICS,
  ["category", "Category"],
  ["status", "Status"],
  ["contract_kind", "Contract"],
  ["contract_type", "Contract type"],
  ["settle_coin", "Settle coin"],
];
function renderHeaders() {
  const head = $("#catalog-head");
  head.replaceChildren(el("th", "Select"));
  for (const [key, label] of HEADERS) {
    const th = el("th"),
      button = el(
        "button",
        label +
          (state.sort === key
            ? { asc: " ASC", desc: " DESC", none: "" }[state.direction] || ""
            : ""),
      );
    th.scope = "col";
    th.setAttribute(
      "aria-sort",
      state.sort === key
        ? { asc: "ascending", desc: "descending", none: "none" }[
            state.direction
          ] || "none"
        : "none",
    );
    button.setAttribute("aria-label", `Sort ${label}`);
    button.onclick = () => {
      Object.assign(state, nextSort(state.sort, state.direction, key));
      persistView();
      renderHeaders();
      loadInstruments();
    };
    th.append(button);
    head.append(th);
  }
  head.append(el("th", "Downloads / coverage"));
}
function epoch(value) {
  return typeof value === "number" ? iso(Math.round(value * 1000)) : "Unknown";
}
let searchTimer;
async function loadInstruments(
  offset = 0,
  cached = false,
  pinned = false,
  conflictRecovered = false,
) {
  state.instrumentAbort?.abort();
  const abort = new AbortController();
  state.instrumentAbort = abort;
  if (!cached)
    $("#screener-status").textContent = "Reading shared catalog snapshot?";
  persistView();
  try {
    const emptySaved = $("#watchlist").checked && !state.saved.size;
    const p = await request(
      `/api/market-catalog/${$("#category").value}`,
      {
        search: $("#search").value,
        sort: state.sort,
        direction: state.direction,
        status: $("#market-status").value,
        contract_kind: $("#contract-kind").value || null,
        watchlist: $("#watchlist").checked ? [...state.saved] : null,
        offset,
        limit: 50,
        revision: pinned || (cached && offset > 0) ? state.revision : null,
      },
      { signal: abort.signal },
    );
    if (abort.signal.aborted) return;
    const d = p.data;
    if (
      !Array.isArray(d.rows) ||
      d.rows.length > 50 ||
      !Number.isSafeInteger(d.total)
    )
      throw Error("Corrupt catalog page");
    if (
      d.rows.some(
        (r) =>
          !r ||
          !/^[-A-Z0-9_]{1,40}$/.test(r.symbol) ||
          !["linear", "inverse"].includes(r.category),
      )
    )
      throw Error("Corrupt catalog identity");
    state.rows = emptySaved ? [] : d.rows;
    state.total = emptySaved ? 0 : d.total;
    state.offset = offset;
    state.revision = d.revision;
    state.catalog = d.catalog || {};
    if (!cached) $("#table-scroll").scrollTop = 0;
    const c = state.catalog,
      a = d.attempt || {};
    $("#screener-status").textContent =
      `${conflictRecovered ? "Snapshot changed; restarted at the first page. Filters and selection retained. " : ""}Loaded ${c.loaded ?? "unknown"} | expected ${c.expected_total ?? "unknown"} | catalog ${c.completeness || "UNKNOWN"} | attempt ${a.completeness || "UNKNOWN"} | ${c.state || "UNAVAILABLE"} | last success ${epoch(c.last_success)} | last error ${c.last_error || a.last_error || "none"}`;
    $("#page-info").textContent =
      `${state.total ? offset + 1 : 0}-${Math.min(offset + state.rows.length, state.total)} of ${state.total} matches | global sorting | unavailable last | revision ${d.revision?.slice(0, 8) || "none"}`;
    $("#previous").disabled = offset === 0;
    $("#next").disabled = offset + 50 >= state.total;
    renderRows();
  } catch (e) {
    if (e.name === "AbortError" || abort.signal.aborted) return;
    if (e.status === 409 && !conflictRecovered) {
      state.offset = 0;
      state.revision = null;
      await loadInstruments(0, false, false, true);
      return;
    }
    if (conflictRecovered) {
      $("#next").disabled = true;
      $("#previous").disabled = true;
    }
    $("#screener-status").textContent =
      `${e.status === 409 ? "Snapshot changed again; automatic retry stopped. Retry manually. " : ""}${e.message}${state.rows.length ? " | retained rows are STALE; retry to verify" : ""}`;
  }
}
$("#search").addEventListener("input", () => {
  persistView();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadInstruments(), 180);
});
for (const id of FILTERS.filter((id) => id !== "search"))
  $("#" + id).addEventListener("change", () => loadInstruments());
$("#category").addEventListener("change", loadSources);
$("#refresh").onclick = async () => {
  const category = $("#category").value;
  $("#refresh").disabled = true;
  try {
    await request(
      `/api/market-catalog/${category}/refresh`,
      {},
      { method: "POST" },
    );
    $("#screener-status").textContent =
      "Shared refresh queued; cached snapshot remains visible.";
    await loadInstruments(state.offset, true);
  } catch (e) {
    $("#screener-status").textContent = e.message;
  } finally {
    $("#refresh").disabled = false;
  }
};
$("#next").onclick = () => loadInstruments(state.offset + 50, false, true);
$("#previous").onclick = () =>
  loadInstruments(Math.max(0, state.offset - 50), false, true);
$("#clear").onclick = () => {
  state.selected.clear();
  renderRows();
};
$("#save").onclick = () => {
  state.saved = new Set(state.selected);
  save("bybit-watchlist", [...state.saved]);
  if ($("#watchlist").checked) loadInstruments();
};
renderHeaders();
setInterval(() => {
  if (!document.hidden) loadInstruments(state.offset, true);
}, 30000);
setInterval(() => {
  const at = state.catalog.next_refresh_at;
  $("#refresh-countdown").textContent =
    typeof at === "number"
      ? `Shared refresh in ${Math.max(0, Math.ceil(at - Date.now() / 1000))}s`
      : `Shared refresh schedule unknown`;
}, 1000);
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
  return progress(j);
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
      el("span", "Download " + jobState(j), "badge"),
      el("span", "Coverage " + jobProgress(j).coverage, "badge"),
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
          ? "Unknown total or completion checks pending - work progress indeterminate"
          : `${p.percent}% - ${p.work_complete ? "all batches acknowledged and record checks passed" : "measured download work"}`,
      ),
    );
    if (jobState(j) === "COMPLETE") complete++;
    const counters = p;
    box.append(
      el(
        "p",
        `Bytes received: ${counters.bytes_received ?? "unknown"} - Records ingested: ${counters.records_ingested ?? "unknown"} - Records verified: ${counters.records_verified ?? "unknown"} - Elapsed: ${counters.elapsed_seconds ?? "unknown"} s - Speed: ${counters.bytes_per_second ?? "unknown"} B/s - Source: ${counters.source}`,
      ),
    );
    box.append(
      el(
        "small",
        "Verified records passed committed value/provenance reread checks; this does not establish dataset coverage.",
      ),
    );
    if (p.evidence_refs.length)
      box.append(el("small", "Evidence: " + p.evidence_refs.join("; ")));
    const sources = j.sources || [
      {
        source:
          p.source !== "unknown" ? p.source : j.request?.source || "unknown",
        checkpoint: j.checkpoint,
        state: jobState(j),
        error: j.error,
      },
    ];
    for (const s of sources) {
      const sp = jobProgress(s);
      const row = el("div", null, "job-source");
      row.append(
        el("strong", `${s.source} · ${s.state || "UNKNOWN"}`),
        el(
          "div",
          `Bytes received ${sp.bytes_received ?? "unknown"} - Records verified ${sp.records_verified ?? "unknown"} - Speed ${sp.bytes_per_second ?? "unknown"} B/s`,
        ),
      );
      const sb = el("progress");
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
          state.jobs.set(j.id, {
            ...p.data,
            request: p.data.request || state.jobRequests[j.id] || j.request,
          });
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
  renderRows();
  $("#overall").textContent = all.length
    ? `${complete}/${all.length} downloads complete - dataset coverage is reported separately on each download`
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
            if (p.data?.id !== id) throw Error("Corrupt job identity");
            if ((jobVersions.get(id) || 0) === version)
              state.jobs.set(id, {
                ...p.data,
                request: p.data.request || state.jobRequests[id],
              });
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
async function submitDownloads(keys) {
  const button = $("#download");
  button.disabled = true;
  try {
    const [start_ms, end_ms] = range(),
      source = $("#source").value,
      kind = $("#kind").value,
      interval = $("#interval").value;
    if (!source)
      throw Error(
        "DATA_UNAVAILABLE: no connected source. Retry source discovery.",
      );
    const kinds = JSON.parse(
      $("#source").selectedOptions[0].dataset.kinds || "[]",
    );
    if (!kinds.includes(kind))
      throw Error("Selected source does not support " + kind);
    if (keys.length > 100)
      throw Error("Choose at most 100 instruments per batch.");
    const errors = [];
    for (const key of keys) {
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
          delete state.jobRequests[old];
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
              interval,
              start_ms,
              end_ms,
            },
          },
        );
        if (!p.data?.id) throw Error("Corrupt job response");
        state.jobRequests[p.data.id] = {
          category,
          symbol,
          source,
          kind,
          interval,
          start_ms,
          end_ms,
        };
        save("bybit-job-requests", state.jobRequests);
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
            delete state.jobRequests[oldest];
          }
        }
        save("bybit-jobs", [...state.ids]);
        state.jobs.set(p.data.id, {
          ...p.data,
          request: p.data.request || state.jobRequests[p.data.id],
        });
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
}
$("#download").onclick = () => submitDownloads([...state.selected]);
// Capability discovery is scoped to the selected instrument and interval.
// A failed or absent discovery response never authorizes speculative API calls.
async function discoverCapabilities(q, signal) {
  const key = JSON.stringify([q.category, q.symbol, q.interval, q.start_ms, q.end_ms]);
  if (state.capabilityKey === key && state.capabilities) return state.capabilities;
  state.capabilities = null;
  state.capabilityKey = null;
  state.capabilityError = null;
  try {
    const payload = await request("/api/capabilities", {
      category: q.category, symbol: q.symbol, interval: q.interval,
      start_ms: q.start_ms, end_ms: q.end_ms,
    }, { signal });
    if (!payload.data || typeof payload.data !== "object" ||
        Array.isArray(payload.data)) throw Error("Invalid capability envelope");
    state.capabilities = payload.data.panels && typeof payload.data.panels === "object" && !Array.isArray(payload.data.panels)
      ? payload.data.panels : payload.data;
  } catch (error) {
    if (signal.aborted) throw error;
    // Discovery is optional until the backend implements its verified contract.
    // Suppress repeat 404/503 requests for the same selection.
    state.capabilities = {};
    state.capabilityError = error.status === 404
      ? "capability_discovery_not_implemented"
      : error.status === 503
        ? "capability_service_unavailable"
        : "capability_discovery_failed";
  }
  state.capabilityKey = key;
  return state.capabilities;
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
  const sourceCadence = (kind) =>
    kind === "funding"
      ? "event"
      : kind === "open_interest"
        ? $("#oi-source-interval").value
        : null;
  const cadences = new Map(PANELS.map(([kind]) => [kind, sourceCadence(kind)]));
  for (const [kind, p] of state.charts) {
    const queryKey = JSON.stringify({
      ...q,
      source_interval: cadences.get(kind),
    });
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
  const capabilities = await discoverCapabilities(q, abort.signal).catch(() => ({}));
  if (abort.signal.aborted) return;
  const cache = new Map();
  let unavailable = 0;
  await Promise.allSettled(
    PANELS.map(async ([kind, , type]) => {
      const p = state.charts.get(kind);
      try {
        const capability = classifyPanelCapability(kind, capabilities[kind]);
        if (!capability.requestAllowed) {
          unavailable++;
          p.originBadge.textContent = "not verified";
          p.message.textContent = `DATA_UNAVAILABLE · ${capability.reason === "capability_not_advertised" ? (state.capabilityError || capability.reason) : capability.reason}`;
          p.meta.textContent = `${capability.state} · ${capability.reason} · no request sent`;
          p.proof.textContent = JSON.stringify({ capability, discovery: state.capabilityError || null }, null, 2);
          return;
        }
        const path = capability.resource;
        if (!/^\/api\/[a-zA-Z0-9_/-]+$/.test(path) || path.includes(".."))
          throw Error("Unsafe capability resource");
        const params = {
            ...q,
            ...(cadences.get(kind)
              ? { source_interval: cadences.get(kind) }
              : {}),
            ...(p.page.cursor ? { cursor: p.page.cursor } : {}),
            ...(path === "/api/analytics" ? { metric: kind } : {}),
            ...(kind === "mark" ? { candle_kind: "official_mark" } : {}),
            ...(kind === "index" ? { candle_kind: "official_index" } : {}),
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
        if (cadences.get(kind))
          p.meta.textContent += ` · source ${cadences.get(kind)} · display ${q.interval}`;
        p.proof.textContent = JSON.stringify(
          {
            capability,
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
        p.chart.set(
          series.rows,
          [q.start_ms, q.end_ms],
          intervalMs(),
          cadences.get(kind),
        );
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
    if (PANELS.some(([kind]) =>
      classifyPanelCapability(kind, capabilities[kind]).requestAllowed
    )) {
      loadQuality(q, abort.signal);
    } else {
      $("#quality-data").textContent =
        "UNVERIFIED · No qualified source for this selection; coverage was not requested.";
    }
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
$("#oi-source-interval").onchange = () => {
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

mountStrategyWorkspace(document.querySelector("#strategy-panels"), {
  getSelection: () => ({ ...query(), catalog_revision: state.revision }),
});
