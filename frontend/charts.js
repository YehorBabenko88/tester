// Only projection to pixels uses Number; all source strings remain untouched.
import { bucketWidth } from "./time.js";
export function plotGeometry(width, range) {
  const left = 8,
    span = Math.max(1, width - 56),
    [a, b] = range;
  return {
    x: (ts) => left + ((ts - a) / (b - a)) * span,
    timeAt: (px) => a + Math.max(0, Math.min(1, (px - left) / span)) * (b - a),
    span,
  };
}
export function barDomain(values, kind) {
  let min = Infinity,
    max = -Infinity;
  for (const n of values) {
    min = Math.min(min, n);
    max = Math.max(max, n);
  }
  if (["volume", "delta", "funding"].includes(kind)) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  return [min, max];
}
export class PanelChart {
  constructor(canvas, kind, onRange, onHover) {
    this.canvas = canvas;
    this.kind = kind;
    this.onRange = onRange;
    this.onHover = onHover;
    this.rows = [];
    canvas.tabIndex = 0;
    canvas.addEventListener("keydown", (e) => {
      if (!this.range) return;
      const names = {
        ArrowLeft: "pan-left",
        ArrowRight: "pan-right",
        "+": "zoom-in",
        "=": "zoom-in",
        "-": "zoom-out",
      };
      if (names[e.key]) {
        e.preventDefault();
        document.getElementById(names[e.key]).click();
      }
    });
    this.observer = new ResizeObserver(() => this.draw());
    this.observer.observe(canvas);
    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        if (!this.range) return;
        const [a, b] = this.range;
        const geometry = plotGeometry(canvas.clientWidth, this.range);
        const ratio = (geometry.timeAt(e.offsetX) - a) / (b - a);
        const factor = e.deltaY > 0 ? 1.4 : 0.7;
        const span = (b - a) * factor;
        const anchor = a + (b - a) * ratio;
        onRange(
          Math.max(0, Math.round(anchor - span * ratio)),
          Math.round(anchor + span * (1 - ratio)),
        );
      },
      { passive: false },
    );
    canvas.addEventListener("pointerdown", (e) => {
      if (this.range) {
        this.drag = {
          x: e.offsetX,
          range: [...this.range],
          select: e.shiftKey,
        };
        canvas.setPointerCapture(e.pointerId);
      }
    });
    canvas.addEventListener("pointerup", (e) => {
      if (!this.drag) return;
      const d = this.drag;
      this.drag = null;
      const span = d.range[1] - d.range[0];
      if (Math.abs(e.offsetX - d.x) < 4) return;
      if (d.select) {
        const geometry = plotGeometry(canvas.clientWidth, d.range);
        const a = geometry.timeAt(Math.min(d.x, e.offsetX)),
          b = geometry.timeAt(Math.max(d.x, e.offsetX));
        onRange(Math.round(a), Math.round(b));
      } else {
        const delta = Math.round(
          ((d.x - e.offsetX) / plotGeometry(canvas.clientWidth, d.range).span) *
            span,
        );
        onRange(Math.max(0, d.range[0] + delta), d.range[1] + delta);
      }
    });
    canvas.addEventListener("pointermove", (e) => {
      if (this.range)
        onHover(
          Math.round(
            plotGeometry(canvas.clientWidth, this.range).timeAt(e.offsetX),
          ),
        );
    });
    canvas.addEventListener("pointerleave", () => onHover(null));
  }
  set(rows, range, interval) {
    this.rows = rows;
    this.range = range;
    this.interval = interval;
    this.draw();
  }
  hover(ts) {
    this.hoverTs = ts;
    this.draw();
  }
  draw() {
    const c = this.canvas,
      ctx = c.getContext("2d"),
      w = c.clientWidth,
      h = c.clientHeight;
    if (!w || !h) return;
    const dpr = devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    if (!this.range || !this.rows.length) return;
    const [a, b] = this.range,
      pad = 48,
      plotW = w - pad - 8,
      plotH = h - 26,
      x = plotGeometry(w, this.range).x;
    const nums = [];
    for (const r of this.rows) {
      if (this.kind === "candles") nums.push(Number(r.low), Number(r.high));
      else if (this.kind === "footprint")
        nums.push(...r.levels.map((l) => Number(l.price)));
      else if (this.kind === "profile") nums.push(Number(r.price));
      else nums.push(Number(r.value));
    }
    let [min, max] = barDomain(nums, this.kind);
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return;
    }
    if (min === max) {
      min -= Math.max(Math.abs(min) * 0.001, 1);
      max += Math.max(Math.abs(max) * 0.001, 1);
    }
    const y = (n) => 6 + ((max - n) / (max - min)) * (plotH - 12);
    ctx.font = "10px Consolas";
    for (let i = 0; i < 4; i++) {
      const n = min + ((max - min) * i) / 3,
        yy = y(n);
      ctx.strokeStyle = "#223044";
      ctx.beginPath();
      ctx.moveTo(0, yy);
      ctx.lineTo(plotW + 8, yy);
      ctx.stroke();
      ctx.fillStyle = "#8796ac";
      ctx.fillText(n.toPrecision(5), plotW + 11, yy + 3);
    }
    for (let i = 0; i < 4; i++) {
      const ts = a + ((b - a) * i) / 3;
      ctx.fillStyle = "#8796ac";
      ctx.fillText(
        new Date(ts).toISOString().slice(5, 16).replace("T", " "),
        Math.min(w - 110, x(ts)),
        h - 4,
      );
    }
    const barWidth = (ts) =>
      Math.max(
        1,
        Math.min(
          16,
          ((plotW * bucketWidth(ts, this.interval)) / (b - a)) * 0.65,
        ),
      );
    if (this.kind === "profile") {
      const peak = Math.max(...this.rows.map((r) => Number(r.volume)), 1);
      for (const r of this.rows) {
        ctx.fillStyle = r.is_poc ? "#f3c273" : "#469e94";
        ctx.fillRect(
          8,
          y(Number(r.price)) - 3,
          (Number(r.volume) / peak) * plotW,
          6,
        );
      }
    } else if (this.kind === "footprint") {
      let peak = 1;
      for (const r of this.rows)
        for (const l of r.levels)
          peak = Math.max(peak, Number(l.taker_buy), Number(l.taker_sell));
      for (const r of this.rows)
        for (const l of r.levels) {
          ctx.fillStyle = `rgba(97,213,191,${0.2 + (0.8 * Number(l.taker_buy)) / peak})`;
          ctx.fillRect(
            x(r.ts),
            y(Number(l.price)) - 5,
            Math.max(4, barWidth(r.ts)),
            9,
          );
          ctx.fillStyle = `rgba(246,132,138,${0.2 + (0.8 * Number(l.taker_sell)) / peak})`;
          ctx.fillRect(
            x(r.ts) - Math.max(4, barWidth(r.ts)),
            y(Number(l.price)) - 5,
            Math.max(4, barWidth(r.ts)),
            9,
          );
        }
    } else if (this.kind === "candles") {
      for (const r of this.rows) {
        ctx.strokeStyle =
          Number(r.close) >= Number(r.open) ? "#61d5bf" : "#f6848a";
        ctx.fillStyle = ctx.strokeStyle;
        ctx.setLineDash(r.is_closed === false ? [2, 2] : []);
        ctx.beginPath();
        ctx.moveTo(x(r.ts), y(Number(r.high)));
        ctx.lineTo(x(r.ts), y(Number(r.low)));
        ctx.stroke();
        ctx.fillRect(
          x(r.ts) - barWidth(r.ts) / 2,
          y(Math.max(Number(r.open), Number(r.close))),
          barWidth(r.ts),
          Math.max(1, Math.abs(y(Number(r.open)) - y(Number(r.close)))),
        );
      }
      ctx.setLineDash([]);
    } else if (["volume", "delta", "funding"].includes(this.kind)) {
      const zero = y(Math.max(min, Math.min(max, 0)));
      for (const r of this.rows) {
        const n = Number(r.value);
        ctx.fillStyle = n < 0 ? "#f6848a" : "#61d5bf";
        ctx.fillRect(
          x(r.ts) - barWidth(r.ts) / 2,
          Math.min(y(n), zero),
          barWidth(r.ts),
          Math.max(1, Math.abs(y(n) - zero)),
        );
      }
    } else if (this.kind === "poc") {
      ctx.strokeStyle = "#f3c273";
      for (let i = 0; i < this.rows.length; i++) {
        const r = this.rows[i];
        ctx.beginPath();
        ctx.moveTo(x(r.ts), y(Number(r.value)));
        ctx.lineTo(x(this.rows[i + 1]?.ts ?? b), y(Number(r.value)));
        ctx.stroke();
      }
    } else {
      ctx.strokeStyle = "#61d5bf";
      ctx.beginPath();
      let previous = null;
      for (const r of this.rows) {
        if (
          previous === null ||
          r.ts - previous >
            bucketWidth(previous ?? r.ts, this.interval) * 1.5 ||
          r.gap_before
        )
          ctx.moveTo(x(r.ts), y(Number(r.value)));
        else ctx.lineTo(x(r.ts), y(Number(r.value)));
        previous = r.ts;
      }
      ctx.stroke();
      for (const r of this.rows) {
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.arc(x(r.ts), y(Number(r.value)), 2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    if (this.hoverTs != null && this.hoverTs >= a && this.hoverTs < b) {
      ctx.strokeStyle = "#93a6bf";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x(this.hoverTs), 0);
      ctx.lineTo(x(this.hoverTs), plotH);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}
