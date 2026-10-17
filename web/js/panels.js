// DOM builders for the settings panels. Every control reports user edits through a
// callback and exposes set() for programmatic updates that must not echo back.
import { BAND_COLORS } from "./eq-graph.js";
import * as p from "./protocol.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

let uid = 0;
const nextId = (prefix) => `${prefix}-${++uid}`;

/** A labelled range slider row. */
export function rangeRow({ title, desc, min, max, format = String, ends, onChange }) {
  const id = nextId("range");
  const input = el("input", { type: "range", id, min, max, step: 1 });
  const out = el("output", { for: id });
  const control = el("div", { class: "control" });
  if (ends) control.append(el("span", { class: "end", text: ends[0] }));
  control.append(input);
  if (ends) control.append(el("span", { class: "end", text: ends[1] }));
  control.append(out);
  input.addEventListener("input", () => {
    out.textContent = format(Number(input.value));
    onChange(Number(input.value));
  });
  const row = el("div", { class: "setting" },
    el("div", { class: "what" }, el("label", { for: id, text: title }), desc ? el("p", { text: desc }) : null),
    control);
  return {
    row,
    set(value) {
      input.value = value;
      out.textContent = format(Number(value));
    },
  };
}

/** A radio group rendered as a list of rows. */
export function choiceGroup({ name, label, options, onChange }) {
  const inputs = new Map();
  const rows = el("div", { class: "rows", role: "radiogroup", "aria-label": label });
  for (const { value, title, desc } of options) {
    const input = el("input", { type: "radio", name, value });
    input.addEventListener("change", () => input.checked && onChange(value));
    inputs.set(value, input);
    rows.append(el("label", { class: "choice" }, input, el("div", {}, el("strong", { text: title }), el("span", { text: desc }))));
  }
  return {
    rows,
    set(value) {
      const input = inputs.get(value);
      if (input) input.checked = true;
    },
  };
}

export function group(title, desc, ...content) {
  return el("section", { class: "group" }, el("h2", { text: title }), desc ? el("p", { text: desc }) : null, ...content);
}

export const balanceText = (v) => {
  const game = Math.round((v * 100) / p.BALANCE_MAX);
  return `${100 - game}:${game}`;
};

const NOISE_GATE = [
  [p.NoiseGate.STREAMING, "Streaming", "Schwächste Stufe. Natürlichster Klang, lässt Umgebungsgeräusche durch – für ruhige Studio- und Heim-Umgebungen."],
  [p.NoiseGate.NIGHT, "Night", "Sehr leichtes Gate für leises, natürliches Sprechen – ideal für späte Sessions."],
  [p.NoiseGate.HOME, "Home", "ASTRO-Standard. Für kleine LANs und typische Wohnumgebungen, entfernt die meisten leichten Hintergrundgeräusche."],
  [p.NoiseGate.TOURNAMENT, "Tournament", "Aggressivste Stufe für laute Bühnen. Am wenigsten natürlich und gated zusätzlich den Sidetone."],
];
const MIC_EQ = [
  [p.MicEq.BRIGHT, "Bright", "Hell, mit betonten Höhen"],
  [p.MicEq.AIRY, "Airy", "Offen und luftig"],
  [p.MicEq.WARM, "Warm", "Voller, mit mehr Tiefen"],
];
const toOptions = (list) => list.map(([value, title, desc]) => ({ value, title, desc }));

export function buildMicPanel(panel, act) {
  const level = rangeRow({ title: "USB-Mikrofonpegel", desc: "Lautstärke deines Mikrofons am PC (USB)", min: 0, max: p.SLIDER_MAX,
    onChange: (v) => act.slider(p.Slider.MIC_LEVEL, v) });
  const sidetone = rangeRow({ title: "Sidetone", desc: "Wie laut du dich selbst im Headset hörst", min: 0, max: p.SLIDER_MAX,
    onChange: (v) => act.slider(p.Slider.SIDETONE, v) });
  const gate = choiceGroup({ name: "noise-gate", label: "Noise Gate", options: toOptions(NOISE_GATE), onChange: act.noiseGate });
  const eq = choiceGroup({ name: "mic-eq", label: "Mikrofon-EQ", options: toOptions(MIC_EQ), onChange: act.micEq });
  panel.append(
    group("Pegel", null, el("div", { class: "rows" }, level.row, sidetone.row)),
    group("Noise Gate", "Wie laut du sprechen musst, damit das Mikrofon öffnet.", gate.rows),
    group("Mikrofon-EQ", "Klangcharakter deines Mikrofons.", eq.rows),
  );
  return (s) => {
    level.set(s.live.sliders[p.Slider.MIC_LEVEL] ?? 0);
    sidetone.set(s.live.sliders[p.Slider.SIDETONE] ?? 0);
    gate.set(s.live.noiseGate);
    eq.set(s.live.micEq);
  };
}

export function buildStreamPanel(panel, act) {
  const rows = [
    [p.Slider.STREAM_GAME, "Spiel", "Ausgehender Spiel-Sound"],
    [p.Slider.STREAM_CHAT, "Chat", "Ausgehender Voice-Chat"],
    [p.Slider.STREAM_MIC, "Mikrofon", "Dein Headset-Mikrofon im Stream"],
    [p.Slider.STREAM_AUX, "AUX-Eingang", "Signal vom AUX-Eingang der Basisstation"],
  ].map(([slider, title, desc]) => [slider, rangeRow({ title, desc, min: 0, max: p.SLIDER_MAX, onChange: (v) => act.slider(slider, v) })]);
  panel.append(group("Stream-Port",
    "Mischung am Stream-Ausgang der Basisstation, zum Beispiel zum Streaming-PC oder zur Capture-Karte. Ändert nicht, was du im Headset hörst.",
    el("div", { class: "rows" }, ...rows.map(([, r]) => r.row))));
  return (s) => rows.forEach(([slider, r]) => r.set(s.live.sliders[slider] ?? 0));
}

export function buildDevicePanel(panel, act) {
  const battery = el("span");
  const batteryText = el("output");
  const batteryDesc = el("p");
  const mix = el("span");
  const mixText = el("output");
  const meterRow = (title, desc, fill, out, ends) => el("div", { class: "setting" },
    el("div", { class: "what" }, el("span", { text: title }), desc),
    el("div", { class: "control" }, ends ? el("span", { class: "end", text: ends[0] }) : null,
      el("div", { class: "meter" }, fill), ends ? el("span", { class: "end", text: ends[1] }) : null, out));
  const balance = rangeRow({ title: "Standard-Balance", desc: "Chat/Spiel-Verhältnis beim Einschalten", min: 0, max: p.BALANCE_MAX,
    format: balanceText, ends: ["Chat", "Spiel"], onChange: act.balance });
  const alert = rangeRow({ title: "Hinweistöne", desc: "Lautstärke der Signal- und Ansagetöne", min: 0, max: p.ALERT_MAX,
    format: (v) => `${v} %`, onChange: act.alertVolume });
  const brightness = rangeRow({ title: "LED-Helligkeit", desc: "Beleuchtung der Basisstation", min: p.BRIGHTNESS_MIN,
    max: p.BRIGHTNESS_MAX, onChange: act.brightness });
  const autoOffId = nextId("select");
  const autoOff = el("select", { id: autoOffId, onchange: (e) => act.autoOff(Number(e.target.value)) },
    ...p.AUTO_OFF_MINUTES.map((m, i) => el("option", { value: i, text: `Nach ${m} Minuten` })));
  const facts = { base: el("dd"), build: el("dd"), headset: el("dd") };
  panel.append(
    group("Status", null, el("div", { class: "rows" },
      meterRow("Akku", batteryDesc, battery, batteryText),
      meterRow("Aktuelle Chat/Spiel-Balance", el("p", { text: "Wird an den Balance-Tasten des Headsets eingestellt" }), mix, mixText, ["Chat", "Spiel"]))),
    group("Einstellungen", null, el("div", { class: "rows" }, balance.row, alert.row, brightness.row,
      el("div", { class: "setting" },
        el("div", { class: "what" }, el("label", { for: autoOffId, text: "Automatisch ausschalten" }),
          el("p", { text: "Wenn das Headset nicht bewegt wird" })),
        el("div", { class: "control" }, autoOff)))),
    group("Firmware", null, el("div", { class: "rows" }, el("dl", { class: "facts" },
      el("dt", { text: "Basisstation" }), facts.base, el("dt", { text: "Build-Datum" }), facts.build,
      el("dt", { text: "Headset" }), facts.headset))),
  );
  const status = (s) => {
    battery.style.width = `${s.battery}%`;
    batteryText.textContent = `${s.battery} %`;
    batteryDesc.textContent = !s.headsetOn ? "Headset ausgeschaltet – Wert vom letzten Kontakt"
      : [s.docked ? "In der Ladeschale" : "Headset an", s.charging ? "lädt" : null].filter(Boolean).join(", ");
    mix.style.width = `${(s.mix * 100) / p.BALANCE_MAX}%`;
    mixText.textContent = balanceText(s.mix);
    facts.headset.textContent = s.headsetVersion || "unbekannt (Headset aus)";
  };
  return {
    status,
    all(s) {
      status(s);
      balance.set(s.live.balance);
      alert.set(s.live.alertVolume);
      brightness.set(s.live.brightness);
      autoOff.value = String(s.live.autoOff);
      if (s.base) {
        facts.base.textContent = `${s.base.version}.${s.baseMinor}`;
        facts.build.textContent = s.base.buildDate;
      }
    },
  };
}

const BAND_TITLES = ["Bass", "Band 2", "Band 3", "Band 4", "Höhen"];
const BAND_KINDS = ["Low-Shelf", "Glocke", "Glocke", "Glocke", "High-Shelf"];

/** Five band columns; onEdit(index, {gain|freq|bandwidth}), onSelect(index). */
export function buildBands(container, { onEdit, onSelect }) {
  const cols = BAND_TITLES.map((title, i) => {
    const gain = el("input", { type: "range", min: p.GAIN_MIN, max: p.GAIN_MAX, step: 1, "aria-label": `${title} Pegel` });
    const out = el("output");
    const freq = el("input", { type: "number", min: p.FREQ_MIN, max: p.FREQ_MAX, step: 1, "aria-label": `${title} Frequenz` });
    const peaking = p.PEAKING_BANDS.includes(i);
    const bw = el("input", { type: "number", min: p.BW_MIN, max: p.BW_MAX, step: 0.1, disabled: !peaking,
      placeholder: peaking ? null : "fest", "aria-label": `${title} Bandbreite` });
    gain.addEventListener("input", () => onEdit(i, { gain: Number(gain.value) }));
    freq.addEventListener("change", () => onEdit(i, { freq: Math.round(clampInput(freq)) }));
    bw.addEventListener("change", () => onEdit(i, { bandwidth: Math.round(clampInput(bw) * 10) / 10 }));
    const col = el("div", { class: "band" },
      el("h3", { text: title }), el("span", { class: "kind", text: BAND_KINDS[i] }), out, gain,
      el("label", {}, "Frequenz (Hz)", freq), el("label", {}, "Bandbreite", bw));
    col.style.setProperty("--band", BAND_COLORS[i]); // CSSOM, allowed by the strict CSP
    col.addEventListener("focusin", () => onSelect(i));
    col.addEventListener("pointerdown", () => onSelect(i));
    container.append(col);
    return { col, gain, out, freq, bw, peaking };
  });
  return (bands, selected) => cols.forEach((c, i) => {
    const b = bands[i];
    c.col.classList.toggle("selected", i === selected);
    c.gain.value = b.gain;
    c.out.textContent = b.gain > 0 ? `+${b.gain} dB` : b.gain < 0 ? `−${-b.gain} dB` : "0 dB";
    if (document.activeElement !== c.freq) c.freq.value = b.freq;
    if (document.activeElement !== c.bw) c.bw.value = c.peaking ? b.bandwidth.toFixed(1) : "";
  });
}

function clampInput(input) {
  const v = Number(input.value);
  const lo = Number(input.min);
  const hi = Number(input.max);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : lo;
}
