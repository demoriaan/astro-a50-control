// Interactive EQ curve: drag a handle for gain/frequency, wheel over a handle for bandwidth.
import { logFreqs, responseDb } from "./eq-math.js";
import * as p from "./protocol.js";

const F_LO = 20;
const F_HI = 20000;
const DB_RANGE = 15;
const PAD = { l: 40, r: 16, t: 18, b: 30 };
const HANDLE_R = 10;
const GRID_FREQS = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
const FREQS = logFreqs(F_LO, F_HI, 280);
// Warm for low frequencies, cool for high ones: the colour encodes where a band sits.
export const BAND_COLORS = ["#f4b04a", "#e9cf6a", "#a8d08d", "#6cc7c1", "#7aa7ec"];

const fmtFreq = (f) => (f >= 1000 ? `${f / 1000}k` : `${f}`);

export class EqGraph {
  constructor(canvas, { onChange, onSelect }) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onChange = onChange;
    this.onSelect = onSelect;
    this.bands = [];
    this.selected = 0;
    this.hover = null;
    this.drag = null;
    this.wheelAccu = 0;
    new ResizeObserver(() => this.resize()).observe(canvas);
    canvas.addEventListener("pointerdown", (e) => this.pointerDown(e));
    canvas.addEventListener("pointermove", (e) => this.pointerMove(e));
    canvas.addEventListener("pointerup", (e) => this.pointerUp(e));
    canvas.addEventListener("pointercancel", (e) => this.pointerUp(e));
    canvas.addEventListener("pointerleave", () => { if (!this.drag) this.setHover(null); });
    canvas.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
  }

  setBands(bands, { cancelDrag = false } = {}) {
    if (cancelDrag) this.drag = null;
    if (this.drag) return;
    this.bands = structuredClone(bands);
    this.draw();
  }

  select(index) {
    this.selected = index;
    this.draw();
  }

  // -- geometry -------------------------------------------------------------

  plot() {
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return { x0: PAD.l, y0: PAD.t, w: Math.max(w - PAD.l - PAD.r, 1), h: Math.max(h - PAD.t - PAD.b, 1) };
  }

  x(f) {
    const { x0, w } = this.plot();
    return x0 + (Math.log(f / F_LO) / Math.log(F_HI / F_LO)) * w;
  }

  y(db) {
    const { y0, h } = this.plot();
    return y0 + ((DB_RANGE - Math.max(-DB_RANGE, Math.min(DB_RANGE, db))) / (2 * DB_RANGE)) * h;
  }

  freqAt(px) {
    const { x0, w } = this.plot();
    return F_LO * (F_HI / F_LO) ** ((px - x0) / w);
  }

  dbAt(py) {
    const { y0, h } = this.plot();
    return DB_RANGE - ((py - y0) / h) * 2 * DB_RANGE;
  }

  hit(px, py) {
    let best = null;
    let dist = (HANDLE_R + 10) ** 2;
    this.bands.forEach((band, i) => {
      const d = (this.x(band.freq) - px) ** 2 + (this.y(band.gain) - py) ** 2;
      if (d <= dist) { best = i; dist = d; }
    });
    return best;
  }

  local(e) {
    const r = this.canvas.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  // -- input ----------------------------------------------------------------

  setHover(index) {
    if (index === this.hover) return;
    this.hover = index;
    this.canvas.style.cursor = index === null ? "default" : "grab";
    this.draw();
  }

  pointerDown(e) {
    const index = this.hit(...this.local(e));
    if (index === null) return;
    e.preventDefault();
    this.canvas.setPointerCapture(e.pointerId);
    this.drag = index;
    this.canvas.style.cursor = "grabbing";
    if (index !== this.selected) {
      this.selected = index;
      this.onSelect(index);
    }
    this.draw();
  }

  pointerMove(e) {
    const [px, py] = this.local(e);
    if (this.drag === null) {
      this.setHover(this.hit(px, py));
      return;
    }
    const band = this.bands[this.drag];
    const gain = Math.round(Math.max(p.GAIN_MIN, Math.min(p.GAIN_MAX, this.dbAt(py))));
    const freq = Math.round(Math.max(p.FREQ_MIN, Math.min(p.FREQ_MAX, this.freqAt(px))));
    if (gain !== band.gain || freq !== band.freq) {
      band.gain = gain;
      band.freq = freq;
      this.draw();
      this.onChange(this.drag, { ...band });
    }
  }

  pointerUp(e) {
    if (this.drag === null) return;
    this.drag = null;
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
    this.canvas.style.cursor = this.hover === null ? "default" : "grab";
    this.draw();
  }

  wheel(e) {
    // Only over a peaking-band handle; elsewhere the wheel keeps scrolling the page.
    const index = this.hover;
    if (index === null || !p.PEAKING_BANDS.includes(index)) return;
    e.preventDefault();
    // One wheel notch (100 px, or 3 lines) is one 0.1 step; touchpads accumulate.
    this.wheelAccu += e.deltaMode === 0 ? e.deltaY : e.deltaY * 33;
    const steps = Math.trunc(this.wheelAccu / 100);
    if (!steps) return;
    this.wheelAccu -= steps * 100;
    const band = this.bands[index];
    const width = Math.round(Math.max(p.BW_MIN, Math.min(p.BW_MAX, band.bandwidth - steps * 0.1)) * 10) / 10;
    if (width !== band.bandwidth) {
      band.bandwidth = width;
      this.draw();
      this.onChange(index, { ...band });
    }
  }

  // -- drawing --------------------------------------------------------------

  resize() {
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(this.canvas.clientWidth * dpr);
    this.canvas.height = Math.round(this.canvas.clientHeight * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  draw() {
    const { ctx } = this;
    const css = getComputedStyle(this.canvas);
    const ink = css.getPropertyValue("--graph-ink").trim() || "#e9ecf4";
    const accent = css.getPropertyValue("--accent").trim() || "#f4b04a";
    const { x0, y0, w, h } = this.plot();
    ctx.clearRect(0, 0, this.canvas.clientWidth, this.canvas.clientHeight);
    ctx.font = "12px 'Barlow Semi Condensed', system-ui, sans-serif";
    ctx.textBaseline = "middle";

    ctx.lineWidth = 1;
    for (const f of GRID_FREQS) {
      const px = Math.round(this.x(f)) + 0.5;
      ctx.strokeStyle = withAlpha(ink, 0.07);
      ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y0 + h); ctx.stroke();
      ctx.fillStyle = withAlpha(ink, 0.5);
      ctx.textAlign = "center";
      ctx.fillText(fmtFreq(f), px, y0 + h + 16);
    }
    for (const db of [-12, -6, 0, 6, 12]) {
      const py = Math.round(this.y(db)) + 0.5;
      ctx.strokeStyle = withAlpha(ink, db === 0 ? 0.22 : 0.07);
      ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x0 + w, py); ctx.stroke();
      ctx.fillStyle = withAlpha(ink, 0.5);
      ctx.textAlign = "right";
      ctx.fillText(db > 0 ? `+${db}` : `${db}`.replace("-", "−"), x0 - 8, py);
    }
    if (!this.bands.length) return;

    const active = [this.selected, this.hover, this.drag];
    this.bands.forEach((band, i) => {
      if (!p.PEAKING_BANDS.includes(i) || !active.includes(i)) return;
      const half = band.bandwidth / 2;
      const xa = this.x(band.freq / 2 ** half);
      const xb = this.x(band.freq * 2 ** half);
      ctx.fillStyle = withAlpha(BAND_COLORS[i], 0.08);
      ctx.fillRect(xa, y0, xb - xa, h);
    });

    const db = responseDb(this.bands, FREQS);
    const pts = FREQS.map((f, i) => [this.x(f), this.y(db[i])]);
    const zero = this.y(0);
    const fill = ctx.createLinearGradient(0, y0, 0, y0 + h);
    fill.addColorStop(0, withAlpha(accent, 0.28));
    fill.addColorStop(0.5, withAlpha(accent, 0.06));
    fill.addColorStop(1, withAlpha(accent, 0.28));
    ctx.beginPath();
    ctx.moveTo(pts[0][0], zero);
    for (const [px, py] of pts) ctx.lineTo(px, py);
    ctx.lineTo(pts[pts.length - 1][0], zero);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.save();
    ctx.shadowColor = withAlpha(accent, 0.6);
    ctx.shadowBlur = 12;
    ctx.beginPath();
    pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2.5;
    ctx.stroke();
    ctx.restore();

    this.bands.forEach((band, i) => {
      const px = this.x(band.freq);
      const py = this.y(band.gain);
      const isActive = active.includes(i);
      const r = HANDLE_R + (i === this.selected ? 2 : 0);
      if (isActive) {
        ctx.beginPath(); ctx.arc(px, py, r + 5, 0, 2 * Math.PI);
        ctx.fillStyle = withAlpha(BAND_COLORS[i], 0.25); ctx.fill();
      }
      ctx.beginPath(); ctx.arc(px, py, r, 0, 2 * Math.PI);
      ctx.fillStyle = BAND_COLORS[i]; ctx.fill();
      ctx.fillStyle = "#1c2130";
      ctx.font = "600 12px 'Barlow Semi Condensed', system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(String(i + 1), px, py + 0.5);
      ctx.font = "12px 'Barlow Semi Condensed', system-ui, sans-serif";
    });
  }
}

function withAlpha(hex, alpha) {
  const v = hex.replace("#", "");
  const n = parseInt(v.length === 3 ? v.replace(/./g, "$&$&") : v, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
