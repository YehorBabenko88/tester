import { isDecimal, compareDecimal } from "./model.js";
import { plotGeometry } from "./charts.js";
const time = (v) => Number.isSafeInteger(v) && v >= 0;
const id = (v) => typeof v === "string" && v.length > 0 && v.length <= 256;
export function validateResearchChart(c) {
  if (
    !c ||
    c.schema_version !== "tester.research-chart/2" ||
    c.execution_mode !== "SIMULATED" ||
    c.synthetic !== true ||
    ![
      "run_id",
      "snapshot_id",
      "revision",
      "venue",
      "category",
      "symbol",
      "interval",
    ].every((k) => id(c[k])) ||
    !["start_ms", "end_ms", "cutoff_ms"].every((k) => time(c[k])) ||
    c.start_ms >= c.end_ms ||
    !Array.isArray(c.source_ids) ||
    !c.source_ids.length ||
    !c.source_ids.every((v) => id(v) && v.startsWith("synthetic:"))
  )
    throw Error("Unqualified synthetic research chart");
  if (
    !Array.isArray(c.candles) ||
    !c.candles.length ||
    c.candles.length > 2000 ||
    !Array.isArray(c.markers) ||
    c.markers.length > 500
  )
    throw Error("Unbounded or absent research data");
  let previous = -1;
  for (const b of c.candles) {
    if (
      !time(b.start_ms) ||
      !time(b.end_ms) ||
      b.start_ms < previous ||
      b.start_ms < c.start_ms ||
      b.end_ms > c.end_ms ||
      b.start_ms >= b.end_ms ||
      !["open", "high", "low", "close"].every(
        (k) => isDecimal(b[k]) && Number.isFinite(Number(b[k])),
      ) ||
      compareDecimal(b.low, b.high) > 0 ||
      ["open", "close"].some(
        (k) =>
          compareDecimal(b[k], b.low) < 0 || compareDecimal(b[k], b.high) > 0,
      )
    )
      throw Error("Invalid candle");
    previous = b.end_ms;
  }
  const seen = new Set();
  for (const m of c.markers) {
    if (
      !["fill_id", "order_id", "position_id"].every((k) => id(m[k])) ||
      seen.has(m.fill_id) ||
      !(m.closed_trade_id === null || id(m.closed_trade_id)) ||
      !isDecimal(m.price) ||
      !Number.isFinite(Number(m.price)) ||
      !["entry", "exit"].includes(m.role) ||
      !["buy", "sell"].includes(m.side)
    )
      throw Error("Invalid fill identity or decimal");
    if (
      ![
        "timestamp_ms",
        "known_at_ms",
        "window_start_ms",
        "window_end_ms",
      ].every((k) => time(m[k])) ||
      m.timestamp_ms < c.start_ms ||
      m.timestamp_ms >= c.end_ms ||
      m.window_start_ms > m.timestamp_ms ||
      m.timestamp_ms >= m.window_end_ms ||
      m.known_at_ms < m.timestamp_ms ||
      m.known_at_ms > c.cutoff_ms ||
      !["bar", "modeled_open"].includes(m.time_precision) ||
      (m.time_precision === "bar" && m.known_at_ms < m.window_end_ms) ||
      (m.time_precision === "modeled_open" &&
        m.timestamp_ms !== m.window_start_ms)
    )
      throw Error("Invalid fill timing");
    seen.add(m.fill_id);
  }
  return c;
}
export function researchGeometry(c, range, width, height) {
  const rows = c.candles.filter(
    (b) => b.start_ms < range[1] && b.end_ms > range[0],
  );
  const markers = c.markers.filter(
    (m) =>
      m.timestamp_ms >= range[0] &&
      m.timestamp_ms < range[1] &&
      m.known_at_ms <= c.cutoff_ms,
  );
  const values = [
    ...rows.flatMap((b) => [Number(b.low), Number(b.high)]),
    ...markers.map((m) => Number(m.price)),
  ];
  let lo = Math.min(...values),
    hi = Math.max(...values);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) {
    lo = 0;
    hi = 1;
  }
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const x = plotGeometry(width, range).x,
    y = (p) => 10 + ((hi - p) / (hi - lo)) * (height - 42);
  return {
    x,
    y,
    rows,
    markers: markers.map((m) => ({
      source: m,
      x: x(m.timestamp_ms),
      y: y(Number(m.price)),
      window: [
        x(Math.max(range[0], m.window_start_ms)),
        x(Math.min(range[1], m.window_end_ms)),
      ],
    })),
    bounds: {
      left: 8,
      right: Math.max(9, width - 48),
      top: 8,
      bottom: height - 26,
    },
  };
}
export class ResearchChart {
  constructor(canvas, onDraw = () => {}) {
    this.canvas = canvas;
    this.onDraw = onDraw;
    canvas.tabIndex = 0;
    this.resize = new ResizeObserver(() => this.draw());
    this.resize.observe(canvas);
    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        if (!this.range) return;
        const [a, b] = this.range;
        const ratio =
            (plotGeometry(canvas.clientWidth, this.range).timeAt(e.offsetX) -
              a) /
            (b - a),
          span = (b - a) * (e.deltaY > 0 ? 1.4 : 0.7),
          anchor = a + (b - a) * ratio;
        this.setRange(anchor - span * ratio, anchor + span * (1 - ratio));
      },
      { passive: false },
    );
    canvas.addEventListener("pointerdown", (e) => {
      if (this.range) {
        this.drag = { x: e.offsetX, range: [...this.range] };
        canvas.setPointerCapture(e.pointerId);
      }
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!this.drag) return;
      const d = this.drag;
      this.drag = null;
      const delta =
        ((d.x - e.offsetX) / plotGeometry(canvas.clientWidth, d.range).span) *
        (d.range[1] - d.range[0]);
      this.setRange(d.range[0] + delta, d.range[1] + delta);
    });
    canvas.addEventListener("keydown", (e) => {
      if (!this.range) return;
      const [a, b] = this.range,
        span = b - a;
      if (["ArrowLeft", "ArrowRight", "+", "=", "-"].includes(e.key)) {
        e.preventDefault();
        if (e.key.startsWith("Arrow")) {
          const d = span * (e.key === "ArrowLeft" ? -0.2 : 0.2);
          this.setRange(a + d, b + d);
        } else {
          const s = span * (e.key === "-" ? 1.4 : 0.7);
          this.setRange((a + b - s) / 2, (a + b + s) / 2);
        }
      }
    });
  }
  set(c) {
    this.data = validateResearchChart(c);
    this.range = [c.start_ms, c.end_ms];
    this.draw();
  }
  setRange(a, b) {
    if (!this.data) return;
    const span = Math.min(
      this.data.end_ms - this.data.start_ms,
      Math.max(1, Math.round(b - a)),
    );
    a = Math.max(
      this.data.start_ms,
      Math.min(this.data.end_ms - span, Math.round(a)),
    );
    this.range = [a, a + span];
    this.draw();
  }
  clear() {
    this.data = null;
    this.range = null;
    this.lastGeometry = null;
    delete this.canvas.dataset.renderedGeometry;
    delete this.canvas.dataset.viewRange;
    this.canvas
      .getContext("2d")
      .clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.canvas.dataset.renderedFillIds = "[]";
  }
  draw() {
    if (!this.data || !this.range) return;
    const c = this.canvas,
      w = c.clientWidth,
      h = c.clientHeight;
    if (!w || !h) return;
    const dpr = globalThis.devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = h * dpr;
    const ctx = c.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    const g = researchGeometry(this.data, this.range, w, h);
    ctx.save();
    ctx.beginPath();
    ctx.rect(
      g.bounds.left,
      g.bounds.top,
      g.bounds.right - g.bounds.left,
      g.bounds.bottom - g.bounds.top,
    );
    ctx.clip();
    for (const b of g.rows) {
      const x = g.x(b.start_ms);
      ctx.strokeStyle =
        compareDecimal(b.close, b.open) >= 0 ? "#61d5bf" : "#f6848a";
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.moveTo(x, g.y(Number(b.high)));
      ctx.lineTo(x, g.y(Number(b.low)));
      ctx.stroke();
      ctx.fillRect(
        x - 3,
        Math.min(g.y(Number(b.open)), g.y(Number(b.close))),
        6,
        Math.max(1, Math.abs(g.y(Number(b.open)) - g.y(Number(b.close)))),
      );
    }
    for (const m of g.markers) {
      ctx.fillStyle = "rgba(243,194,115,.16)";
      ctx.fillRect(
        m.window[0],
        g.bounds.top,
        Math.max(1, m.window[1] - m.window[0]),
        g.bounds.bottom - g.bounds.top,
      );
      ctx.strokeStyle = "#f3c273";
      ctx.fillStyle = "#f3c273";
      ctx.beginPath();
      if (m.source.role === "entry") {
        ctx.moveTo(m.x, m.y - 6);
        ctx.lineTo(m.x - 5, m.y + 5);
        ctx.lineTo(m.x + 5, m.y + 5);
        ctx.closePath();
        ctx.fill();
      } else {
        ctx.moveTo(m.x - 5, m.y - 5);
        ctx.lineTo(m.x + 5, m.y + 5);
        ctx.moveTo(m.x + 5, m.y - 5);
        ctx.lineTo(m.x - 5, m.y + 5);
        ctx.stroke();
      }
    }
    ctx.restore();
    ctx.fillStyle = "#dce5f1";
    ctx.font = "12px Segoe UI";
    ctx.fillText("SIMULATED  |  SYNTHETIC RESEARCH", 10, h - 8);
    this.lastGeometry = g;
    c.dataset.renderedGeometry = JSON.stringify({
      bounds: g.bounds,
      markers: g.markers.map((m) => ({
        fill_id: m.source.fill_id,
        x: m.x,
        y: m.y,
        window: m.window,
      })),
    });
    c.dataset.renderedFillIds = JSON.stringify(
      g.markers.map((m) => m.source.fill_id),
    );
    c.dataset.viewRange = JSON.stringify(this.range);
    this.onDraw(g);
  }
}
export async function researchDigest(value) {
  const canonical = (v) =>
    Array.isArray(v)
      ? "[" + v.map(canonical).join(",") + "]"
      : v !== null && typeof v === "object"
        ? "{" +
          Object.keys(v)
            .sort()
            .map((k) => canonical(k) + ":" + canonical(v[k]))
            .join(",") +
          "}"
        : JSON.stringify(v).replace(
            /[\u007f-\uffff]/g,
            (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
          );
  const bytes = new TextEncoder().encode(canonical(value));
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
